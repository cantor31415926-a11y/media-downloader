from unittest.mock import patch

import pytest
import requests

from downloader.extractor import (
    MediaExtractionError,
    extract_from_html,
    extract_media,
    extract_with_ytdlp,
    parse_html_media,
    validate_url,
)


def test_validate_url_accepts_only_http_urls() -> None:
    assert validate_url(" https://example.com/a ") == "https://example.com/a"
    with pytest.raises(ValueError):
        validate_url("file:///D:/video.mp4")
    with pytest.raises(ValueError):
        validate_url("example.com/video")


def test_parse_html_media_resolves_and_deduplicates_urls() -> None:
    html = '''<html><head><title>示例媒体</title></head><body>
        <video src="/movie.mp4"></video><video src="/movie.mp4"></video>
        <audio><source src="sound.m4a" type="audio/mp4"></audio>
        <track kind="subtitles" srclang="zh-CN" label="中文" src="sub/zh.vtt">
    </body></html>'''
    media = parse_html_media(html, "https://example.com/page/index.html")
    assert media.title == "示例媒体"
    assert [item.url for item in media.formats] == [
        "https://example.com/movie.mp4", "https://example.com/page/sound.m4a"
    ]
    assert media.subtitles[0].url == "https://example.com/page/sub/zh.vtt"
    assert media.subtitles[0].language == "zh-CN"


def test_parse_html_media_rejects_page_without_media() -> None:
    with pytest.raises(MediaExtractionError):
        parse_html_media("<html><title>x</title></html>", "https://example.com/")


def test_html_extraction_reports_network_timeout() -> None:
    with patch("downloader.extractor.requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(MediaExtractionError, match="无法读取网页"):
            extract_from_html("https://example.com", timeout=1)


def test_ytdlp_receives_chrome_cookie_option() -> None:
    raw = {
        "title": "sample",
        "formats": [{"format_id": "1", "ext": "mp4", "vcodec": "h264", "acodec": "aac"}],
    }

    with patch("downloader.extractor.YoutubeDL") as youtube_dl:
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = raw
        extract_with_ytdlp("https://example.com/watch", browser_cookies="chrome")

    assert youtube_dl.call_args.args[0]["cookiesfrombrowser"] == ("chrome",)


def test_cookie_database_lock_has_clear_error() -> None:
    with (
        patch(
            "downloader.extractor.extract_with_ytdlp",
            side_effect=MediaExtractionError("ERROR: Could not copy Chrome cookie database"),
        ),
        patch(
            "downloader.extractor.extract_from_html",
            side_effect=MediaExtractionError("未找到 HTML 媒体"),
        ),
    ):
        with pytest.raises(MediaExtractionError, match="完全退出 Chrome"):
            extract_media("https://example.com/watch", browser_cookies="chrome")


def test_anonymous_failure_preserves_ytdlp_reason() -> None:
    with (
        patch(
            "downloader.extractor.extract_with_ytdlp",
            side_effect=MediaExtractionError("Fresh cookies are needed"),
        ),
        patch(
            "downloader.extractor.extract_from_html",
            side_effect=MediaExtractionError("未找到 HTML 媒体"),
        ),
    ):
        with pytest.raises(MediaExtractionError, match="使用 Chrome Cookie"):
            extract_media("https://example.com/watch")
