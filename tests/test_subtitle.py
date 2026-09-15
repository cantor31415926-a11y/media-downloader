from pathlib import Path
from unittest.mock import patch

import pytest

from config import AppConfig
from downloader.models import MediaInfo, SubtitleInfo
from downloader.subtitle import SubtitleDownloadError, choose_subtitle, download_subtitle


def test_choose_subtitle_is_case_insensitive() -> None:
    media = MediaInfo(
        title="sample",
        source_url="https://example.com",
        source_type="html",
        subtitles=[SubtitleInfo("zh-CN", "中文", "https://example.com/zh.vtt", "vtt")],
    )
    assert choose_subtitle(media, "ZH-cn").name == "中文"


def test_choose_subtitle_prefers_native_vtt() -> None:
    media = MediaInfo(
        title="sample",
        source_url="https://example.com",
        source_type="ytdlp",
        subtitles=[
            SubtitleInfo("zh-Hans", "中文", "https://example.com/zh.json3", "json3", "ytdlp"),
            SubtitleInfo("zh-Hans", "中文", "https://example.com/zh.vtt", "vtt", "ytdlp"),
        ],
    )
    assert choose_subtitle(media, "ZH-hans").extension == "vtt"


def test_choose_subtitle_reports_missing_language() -> None:
    media = MediaInfo(title="sample", source_url="https://example.com", source_type="html")
    with pytest.raises(SubtitleDownloadError, match="未找到"):
        choose_subtitle(media, "zh-CN")


def test_vtt_download_reports_progress(tmp_path: Path) -> None:
    class FakeResponse:
        headers = {"content-length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == 64 * 1024
            return iter((b"WEB", b"VTT"))

    media = MediaInfo(title="sample", source_url="https://example.com", source_type="html")
    subtitle = SubtitleInfo("zh-CN", "中文", "https://example.com/zh.vtt", "vtt")
    updates: list[tuple[str, float | None]] = []

    with patch("downloader.subtitle.requests.get", return_value=FakeResponse()):
        result = download_subtitle(media, subtitle, AppConfig(output_dir=tmp_path), lambda message, percent: updates.append((message, percent)))

    assert result.path == tmp_path / "Subtitle" / "sample.zh-CN.vtt"
    assert result.path.read_bytes() == b"WEBVTT"
    assert updates[0] == ("准备下载字幕…", 0.0)
    assert updates[-1] == ("字幕下载完成", 100.0)
    assert ("正在下载字幕…", 50.0) in updates
