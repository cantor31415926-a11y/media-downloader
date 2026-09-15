"""Public subtitle download and VTT normalization."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import requests

from config import AppConfig
from utils.filename import unique_path

from .models import DownloadResult, MediaInfo, ProgressCallback, SubtitleInfo


class SubtitleDownloadError(RuntimeError):
    """Raised when a public subtitle cannot be downloaded or converted."""


def choose_subtitle(media: MediaInfo, language: str) -> SubtitleInfo:
    normalized = language.lower()
    matches = [subtitle for subtitle in media.subtitles if subtitle.language.lower() == normalized]
    for subtitle in matches:
        if (subtitle.extension or "").lower().lstrip(".") == "vtt":
            return subtitle
    if matches:
        return matches[0]
    raise SubtitleDownloadError(f"未找到语言为 {language} 的字幕。")


def download_subtitle(
    media: MediaInfo,
    subtitle: SubtitleInfo,
    config: AppConfig,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    if not subtitle.url:
        raise SubtitleDownloadError("该字幕没有可访问的公开下载地址。")
    config.ensure_directories()
    task_dir = config.temp_dir / "subtitle"
    task_dir.mkdir(parents=True, exist_ok=True)
    source_extension = (subtitle.extension or "vtt").lstrip(".")
    source_path = task_dir / f"subtitle.{source_extension}"
    if progress_callback:
        progress_callback("准备下载字幕…", 0.0)
    try:
        with requests.get(subtitle.url, stream=True, timeout=config.request_timeout, headers={"User-Agent": "MediaDownloader/1.0"}) as response:
            response.raise_for_status()
            try:
                total = int(response.headers.get("content-length", "0") or 0)
            except (TypeError, ValueError):
                total = 0
            received = 0
            with source_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        output.write(chunk)
                        received += len(chunk)
                        if progress_callback:
                            percent = min(100.0, received * 100 / total) if total else None
                            progress_callback("正在下载字幕…", percent)
    except requests.RequestException as exc:
        raise SubtitleDownloadError(f"字幕下载失败：{exc}") from exc

    target_stem = f"{media.title}.{subtitle.language}"
    destination = unique_path(config.subtitle_dir, target_stem, ".vtt", config.max_filename_length)
    if source_path.suffix.lower() == ".vtt":
        shutil.move(str(source_path), str(destination))
        if progress_callback:
            progress_callback("字幕下载完成", 100.0)
        return DownloadResult(path=destination, kind="subtitle", merged_or_converted=False)
    if progress_callback:
        progress_callback("正在将字幕转换为 VTT…", None)
    convert_to_vtt(source_path, destination, config)
    if progress_callback:
        progress_callback("字幕下载完成", 100.0)
    return DownloadResult(path=destination, kind="subtitle", merged_or_converted=True)


def find_ffmpeg(config: AppConfig) -> str:
    executable = config.resolve_ffmpeg()
    if not executable:
        raise SubtitleDownloadError("未找到 FFmpeg。请安装 FFmpeg 并加入 PATH，或在 config.py 指定 ffmpeg_path。")
    return executable


def convert_to_vtt(source: Path, destination: Path, config: AppConfig) -> None:
    executable = find_ffmpeg(config)
    command = [executable, "-y", "-i", str(source), str(destination)]
    try:
        subprocess.run(command, shell=False, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[-500:]
        raise SubtitleDownloadError(f"FFmpeg 字幕转换失败：{detail or exc.returncode}") from exc
