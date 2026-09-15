"""Safe download orchestration for yt-dlp and direct public media URLs."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from config import AppConfig
from utils.filename import unique_path

from .models import DownloadResult, MediaFormat, MediaInfo, ProgressCallback

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtDlpDownloadError
except ImportError:
    YoutubeDL = None  # type: ignore[assignment,misc]
    YtDlpDownloadError = Exception



class DownloadError(RuntimeError):
    """Raised for safe, user-facing media download failures."""


class MediaDownloader:
    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.progress_callback = progress_callback

    def download_video(self, media: MediaInfo) -> DownloadResult:
        return self._download_media(media, "video")

    def download_audio(self, media: MediaInfo) -> DownloadResult:
        return self._download_media(media, "audio")

    def _download_media(self, media: MediaInfo, kind: str) -> DownloadResult:
        self.config.ensure_directories()
        kind_label = "视频" if kind == "video" else "音频"
        self._report_progress(f"准备下载{kind_label}…", 0.0)
        task_dir = self.config.temp_dir / uuid.uuid4().hex
        task_dir.mkdir(parents=True, exist_ok=False)
        target_dir = self.config.video_dir if kind == "video" else self.config.audio_dir
        extension = ".mp4" if kind == "video" else ".m4a"
        destination = unique_path(target_dir, media.title, extension, self.config.max_filename_length)
        try:
            if media.source_type == "ytdlp":
                self._download_with_ytdlp(media, kind, task_dir, destination)
            else:
                selected = self._best_html_format(media, kind)
                downloaded = self._download_direct_file(selected, task_dir)
                if kind == "video" and downloaded.suffix.lower() != ".mp4":
                    self._convert(downloaded, destination, ["-c", "copy"])
                elif kind == "audio" and downloaded.suffix.lower() != ".m4a":
                    self._convert(downloaded, destination, ["-vn", "-c:a", "aac"])
                else:
                    shutil.move(str(downloaded), str(destination))
            self._report_progress(f"{kind_label}下载完成", 100.0)
            return DownloadResult(path=destination, kind=kind, merged_or_converted=True)
        except Exception:
            if not self.config.keep_failed_temp_files:
                shutil.rmtree(task_dir, ignore_errors=True)
            raise
        finally:
            if task_dir.exists() and destination.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def _download_with_ytdlp(self, media: MediaInfo, kind: str, task_dir: Path, destination: Path) -> None:
        if YoutubeDL is None:
            raise DownloadError("未安装 yt-dlp；请运行 pip install -r requirements.txt。")
        if kind == "video":
            format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            format_selector = "bestaudio[ext=m4a]/bestaudio"
        options = {
            "format": format_selector,
            "outtmpl": str(task_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "nooverwrites": True,
            "quiet": True,
            "progress_hooks": [lambda data: self._progress_hook(data, kind)],
            "merge_output_format": "mp4" if kind == "video" else None,
            "postprocessors": ([{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}] if kind == "audio" else []),
        }
        if urlparse(media.source_url).hostname in {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"}:
            options["source_address"] = "0.0.0.0"
        if node_path := shutil.which("node"):
            options["js_runtimes"] = {"node": {"path": node_path}}
        ffmpeg_path = self.config.resolve_ffmpeg()
        if ffmpeg_path:
            options["ffmpeg_location"] = ffmpeg_path
        if self.config.browser_cookies:
            options["cookiesfrombrowser"] = (self.config.browser_cookies,)
        try:
            for attempt in range(5):
                try:
                    with YoutubeDL(options) as ydl:
                        ydl.download([media.source_url])
                    break
                except (YtDlpDownloadError, OSError) as exc:
                    if "UNEXPECTED_EOF_WHILE_READING" not in str(exc) or attempt == 4:
                        raise
                    self._report_progress(f"连接中断，正在断点重试（{attempt + 1}/4）…", None)
                    time.sleep(attempt + 1)
        except (YtDlpDownloadError, OSError, ValueError) as exc:
            if (
                urlparse(media.source_url).hostname in {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"}
                and "HTTP Error 403" in str(exc)
            ):
                raise DownloadError(
                    "YouTube 拒绝了视频数据请求（HTTP 403）。"
                    "可能与下载组件版本、播放验证或网络出口有关，不能仅凭此错误确定原因。"
                ) from exc
            raise DownloadError(f"yt-dlp 下载失败：{exc}") from exc
        candidates = [path for path in task_dir.iterdir() if path.is_file() and not path.name.endswith(".part")]
        if not candidates:
            raise DownloadError("yt-dlp 未生成下载文件。")
        preferred = next((path for path in candidates if path.suffix.lower() == destination.suffix.lower()), candidates[0])
        if preferred.suffix.lower() == destination.suffix.lower():
            shutil.move(str(preferred), str(destination))
        elif kind == "video":
            self._convert(preferred, destination, ["-c", "copy"])
        else:
            self._convert(preferred, destination, ["-vn", "-c:a", "aac"])

    def _best_html_format(self, media: MediaInfo, kind: str) -> MediaFormat:
        candidates = media.video_formats if kind == "video" else media.audio_formats
        if not candidates:
            raise DownloadError(f"网页中没有可下载的{ '视频' if kind == 'video' else '音频' }资源。")
        return candidates[0]

    def _download_direct_file(self, media_format: MediaFormat, task_dir: Path) -> Path:
        if not media_format.url:
            raise DownloadError("媒体资源没有公开下载地址。")
        extension = (media_format.extension or "bin").lstrip(".")
        path = task_dir / f"source.{extension}"
        try:
            with requests.get(media_format.url, stream=True, timeout=self.config.request_timeout, headers={"User-Agent": "MediaDownloader/1.0"}) as response:
                response.raise_for_status()
                try:
                    total = int(response.headers.get("content-length", "0") or 0)
                except (TypeError, ValueError):
                    total = 0
                received = 0
                kind_label = "音频" if media_format.kind == "audio" else "视频"
                with path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            output.write(chunk)
                            received += len(chunk)
                            if total:
                                percent = min(100.0, received * 100 / total)
                                self.logger.info("下载进度：%.1f%%", percent)
                                self._report_progress(f"正在下载{kind_label}…", percent)
                            else:
                                self._report_progress(f"正在下载{kind_label}…", None)
        except requests.RequestException as exc:
            raise DownloadError(f"直接媒体下载失败：{exc}") from exc
        return path

    def _convert(self, source: Path, destination: Path, codec_args: list[str]) -> None:
        executable = self.config.resolve_ffmpeg()
        if not executable:
            raise DownloadError("未找到 FFmpeg。请安装 FFmpeg 并加入 PATH，或在 config.py 指定 ffmpeg_path。")
        command = [executable, "-y", "-i", str(source), *codec_args, str(destination)]
        self._report_progress("正在使用 FFmpeg 处理媒体…", None)
        try:
            subprocess.run(command, shell=False, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()[-500:]
            raise DownloadError(f"FFmpeg 处理失败：{detail or exc.returncode}") from exc

    def _progress_hook(self, data: dict, kind: str | None = None) -> None:
        kind_label = "视频" if kind == "video" else "音频" if kind == "audio" else "媒体"
        if data.get("status") == "downloading":
            downloaded = data.get("downloaded_bytes")
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            percent = None
            if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total > 0:
                percent = min(100.0, float(downloaded) * 100 / float(total))
            display_percent = data.get("_percent_str", "").strip()
            if display_percent:
                self.logger.info("下载进度：%s", display_percent)
            self._report_progress(f"正在下载{kind_label}…", percent)
        elif data.get("status") == "finished":
            self._report_progress(f"{kind_label}下载完成，正在处理格式…", None)

    def _report_progress(self, message: str, percent: float | None) -> None:
        if self.progress_callback:
            self.progress_callback(message, percent)
