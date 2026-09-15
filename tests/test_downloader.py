import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from config import AppConfig
from downloader.core import DownloadError, MediaDownloader, YtDlpDownloadError
from downloader.models import MediaFormat, MediaInfo


def test_html_download_moves_matching_direct_file_without_ffmpeg(tmp_path: Path) -> None:
    config = AppConfig(output_dir=tmp_path)
    media = MediaInfo(
        title="sample",
        source_url="https://example.com/page",
        source_type="html",
        formats=[MediaFormat("html-1", "video", "mp4", "https://example.com/video.mp4")],
    )
    downloader = MediaDownloader(config)

    def fake_download(_format: MediaFormat, task_dir: Path) -> Path:
        source = task_dir / "source.mp4"
        source.write_bytes(b"video")
        return source

    with patch.object(downloader, "_download_direct_file", side_effect=fake_download):
        result = downloader.download_video(media)
    assert result.path.read_bytes() == b"video"
    assert result.path.parent == tmp_path / "Video"


def test_conversion_requires_ffmpeg(tmp_path: Path) -> None:
    config = AppConfig(output_dir=tmp_path)
    downloader = MediaDownloader(config)
    source = tmp_path / "source.webm"
    source.write_bytes(b"x")
    with patch("downloader.core.shutil.which", return_value=None):
        with pytest.raises(DownloadError, match="FFmpeg"):
            downloader._convert(source, tmp_path / "output.mp4", ["-c", "copy"])


def test_ytdlp_progress_hook_reports_numeric_progress(tmp_path: Path) -> None:
    updates: list[tuple[str, float | None]] = []
    downloader = MediaDownloader(AppConfig(output_dir=tmp_path), progress_callback=lambda message, percent: updates.append((message, percent)))

    downloader._progress_hook(
        {"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100, "_percent_str": "25.0%"},
        "video",
    )

    assert updates == [("正在下载视频…", 25.0)]


def test_ytdlp_progress_hook_reports_unknown_size(tmp_path: Path) -> None:
    updates: list[tuple[str, float | None]] = []
    downloader = MediaDownloader(AppConfig(output_dir=tmp_path), progress_callback=lambda message, percent: updates.append((message, percent)))

    downloader._progress_hook({"status": "downloading", "downloaded_bytes": 25}, "audio")

    assert updates == [("正在下载音频…", None)]


def test_ffmpeg_failure_includes_error_summary(tmp_path: Path) -> None:
    downloader = MediaDownloader(AppConfig(output_dir=tmp_path))
    source = tmp_path / "source.webm"
    source.write_bytes(b"x")
    failure = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="invalid media")

    with (
        patch("downloader.core.shutil.which", return_value="ffmpeg"),
        patch("downloader.core.subprocess.run", side_effect=failure),
    ):
        with pytest.raises(DownloadError, match="invalid media"):
            downloader._convert(source, tmp_path / "output.mp4", ["-c", "copy"])


def test_ytdlp_download_receives_chrome_cookie_option(tmp_path: Path) -> None:
    config = AppConfig(output_dir=tmp_path, browser_cookies="chrome")
    ffmpeg_dir = tmp_path / "FFmpeg" / "ffmpeg-test" / "bin"
    ffmpeg_dir.mkdir(parents=True)
    ffmpeg = ffmpeg_dir / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    (ffmpeg_dir / "ffprobe.exe").write_bytes(b"")
    downloader = MediaDownloader(config)
    media = MediaInfo(
        title="sample",
        source_url="https://example.com/watch",
        source_type="ytdlp",
        formats=[MediaFormat("1", "combined", "mp4")],
    )
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    destination = tmp_path / "Video" / "sample.mp4"
    destination.parent.mkdir()

    def fake_download(_urls: list[str]) -> None:
        (task_dir / "sample.mp4").write_bytes(b"video")

    with patch("downloader.core.YoutubeDL") as youtube_dl:
        youtube_dl.return_value.__enter__.return_value.download.side_effect = fake_download
        downloader._download_with_ytdlp(media, "video", task_dir, destination)

    assert youtube_dl.call_args.args[0]["cookiesfrombrowser"] == ("chrome",)
    assert youtube_dl.call_args.args[0]["ffmpeg_location"] == str(ffmpeg)
    assert destination.read_bytes() == b"video"


def test_ssl_eof_retries_in_same_directory(tmp_path: Path) -> None:
    downloader = MediaDownloader(AppConfig(output_dir=tmp_path))
    media = MediaInfo(title="sample", source_url="https://youtu.be/example", source_type="ytdlp")
    calls = []

    def download(urls):
        calls.append(urls)
        if len(calls) == 1:
            (tmp_path / "sample.mp4.part").write_bytes(b"partial")
            raise YtDlpDownloadError("UNEXPECTED_EOF_WHILE_READING")
        assert (tmp_path / "sample.mp4.part").read_bytes() == b"partial"
        (tmp_path / "sample.mp4").write_bytes(b"complete")

    with patch("downloader.core.YoutubeDL") as ydl, patch("downloader.core.time.sleep"):
        ydl.return_value.__enter__.return_value.download.side_effect = download
        downloader._download_with_ytdlp(media, "video", tmp_path, tmp_path / "output.mp4")
    assert len(calls) == 2
    assert (tmp_path / "output.mp4").read_bytes() == b"complete"


def test_youtube_403_explains_network_restriction(tmp_path: Path) -> None:
    downloader = MediaDownloader(AppConfig(output_dir=tmp_path))
    media = MediaInfo(title="sample", source_url="https://youtu.be/example", source_type="ytdlp")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    with patch("downloader.core.YoutubeDL") as youtube_dl:
        youtube_dl.return_value.__enter__.return_value.download.side_effect = YtDlpDownloadError("HTTP Error 403: Forbidden")
        with pytest.raises(DownloadError, match="网络出口"):
            downloader._download_with_ytdlp(media, "video", task_dir, tmp_path / "sample.mp4")
