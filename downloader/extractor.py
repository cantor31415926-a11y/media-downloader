"""Metadata extraction from yt-dlp supported URLs and ordinary HTML pages."""

from __future__ import annotations

import logging
import mimetypes
import shutil
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import MediaFormat, MediaInfo, SubtitleInfo

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtDlpDownloadError
except ImportError:  # Allows unit tests and a useful install message before dependencies are installed.
    YoutubeDL = None  # type: ignore[assignment,misc]
    YtDlpDownloadError = Exception



class MediaExtractionError(RuntimeError):
    """Raised when a URL cannot be analysed for accessible public media."""


def validate_url(value: str) -> str:
    """Validate and normalize an HTTP(S) URL without fetching it."""
    candidate = (value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 http:// 或 https:// 网页 URL。")
    return candidate


def extract_media(
    url: str,
    timeout: int = 30,
    logger: logging.Logger | None = None,
    browser_cookies: str | None = None,
) -> MediaInfo:
    """Use yt-dlp first, then fall back to static HTML media tags."""
    url = validate_url(url)
    try:
        return extract_with_ytdlp(url, browser_cookies=browser_cookies)
    except MediaExtractionError as error:
        ytdlp_error = error
        if logger:
            logger.info("yt-dlp 未识别该页面，改用 HTML 扫描：%s", ytdlp_error)
    try:
        return extract_from_html(url, timeout=timeout)
    except MediaExtractionError as html_error:
        if browser_cookies:
            raise MediaExtractionError(_browser_cookie_error_message(ytdlp_error, html_error)) from ytdlp_error
        raise MediaExtractionError(_anonymous_error_message(ytdlp_error, html_error)) from html_error


def extract_with_ytdlp(url: str, browser_cookies: str | None = None) -> MediaInfo:
    if YoutubeDL is None:
        raise MediaExtractionError("未安装 yt-dlp；请运行 pip install -r requirements.txt。")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if node_path := shutil.which("node"):
        options["js_runtimes"] = {"node": {"path": node_path}}
    if browser_cookies:
        options["cookiesfrombrowser"] = (browser_cookies,)
    try:
        with YoutubeDL(options) as ydl:
            raw = ydl.extract_info(url, download=False)
    except (YtDlpDownloadError, OSError, ValueError) as exc:
        raise MediaExtractionError(str(exc)) from exc
    if not raw:
        raise MediaExtractionError("yt-dlp 未返回媒体信息。")
    if raw.get("_type") == "playlist":
        entries = [entry for entry in raw.get("entries", []) if entry]
        if len(entries) != 1:
            raise MediaExtractionError("第一版不支持播放列表 URL，请提供单个媒体页面。")
        raw = entries[0]

    formats = [_format_from_ytdlp(item) for item in raw.get("formats", []) if item.get("format_id")]
    subtitles = _subtitles_from_ytdlp(raw)
    if not formats and not subtitles:
        raise MediaExtractionError("页面中没有可下载的公开媒体或字幕。")
    return MediaInfo(
        title=raw.get("title") or "untitled",
        source_url=url,
        source_type="ytdlp",
        formats=formats,
        subtitles=subtitles,
        webpage_url=raw.get("webpage_url") or url,
    )


def _browser_cookie_error_message(ytdlp_error: Exception, html_error: Exception) -> str:
    detail = str(ytdlp_error)
    if "Could not copy" in detail and "cookie database" in detail.lower():
        return (
            "无法读取 Chrome Cookie 数据库。请先完全退出 Chrome（包括后台进程），"
            "再重新点击“解析”；下载完成前不要重新打开 Chrome。"
        )
    if "failed to load cookies" in detail.lower():
        return "无法读取 Chrome Cookie。请确认 Chrome 已完全退出，然后重试。"
    return f"使用 Chrome Cookie 后 yt-dlp 仍解析失败：{detail}\nHTML 扫描结果：{html_error}"


def _anonymous_error_message(ytdlp_error: Exception, html_error: Exception) -> str:
    detail = str(ytdlp_error)
    if "fresh cookies" in detail.lower():
        return (
            "该网站要求浏览器 Cookie。请确认内容能在你的 Chrome 中正常访问，"
            "然后勾选“使用 Chrome Cookie（仅当前运行）”并重新解析。"
        )
    return f"yt-dlp 解析失败：{detail}\nHTML 扫描也未找到媒体：{html_error}"


def _format_from_ytdlp(raw: dict) -> MediaFormat:
    video_codec = raw.get("vcodec")
    audio_codec = raw.get("acodec")
    has_video = video_codec and video_codec != "none"
    has_audio = audio_codec and audio_codec != "none"
    kind = "combined" if has_video and has_audio else "video" if has_video else "audio"
    return MediaFormat(
        format_id=str(raw["format_id"]),
        kind=kind,
        extension=raw.get("ext"),
        width=raw.get("width"),
        height=raw.get("height"),
        video_codec=video_codec,
        audio_codec=audio_codec,
        filesize=raw.get("filesize") or raw.get("filesize_approx"),
        description=raw.get("format_note"),
    )


def _subtitles_from_ytdlp(raw: dict) -> list[SubtitleInfo]:
    result: list[SubtitleInfo] = []
    subtitle_sets = (raw.get("subtitles", {}), raw.get("automatic_captions", {}))
    seen: set[tuple[str, str]] = set()
    for subtitle_map in subtitle_sets:
        for language, entries in subtitle_map.items():
            for entry in entries or []:
                url = entry.get("url")
                key = (language, url or "")
                if not url or key in seen:
                    continue
                seen.add(key)
                result.append(SubtitleInfo(
                    language=language,
                    name=entry.get("name") or language,
                    url=url,
                    extension=entry.get("ext"),
                    source="ytdlp",
                ))
    return result


def extract_from_html(url: str, timeout: int = 30) -> MediaInfo:
    """Scan public, statically-present HTML media elements. JavaScript players are out of scope."""
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "MediaDownloader/1.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MediaExtractionError(f"无法读取网页：{exc}") from exc
    return parse_html_media(response.text, response.url)


def parse_html_media(html: str, page_url: str) -> MediaInfo:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "untitled")
    formats: list[MediaFormat] = []
    subtitles: list[SubtitleInfo] = []
    seen_media: set[str] = set()
    seen_subtitles: set[str] = set()

    for tag in soup.find_all(["video", "audio", "source"]):
        src = tag.get("src")
        if not src:
            continue
        absolute_url = _public_absolute_url(src, page_url)
        if not absolute_url or absolute_url in seen_media:
            continue
        seen_media.add(absolute_url)
        parent_name = tag.parent.name if tag.name == "source" and tag.parent else tag.name
        kind = "audio" if parent_name == "audio" else "video"
        extension = _extension_from_url_or_type(absolute_url, tag.get("type"))
        formats.append(MediaFormat(
            format_id=f"html-{len(formats) + 1}", kind=kind, extension=extension, url=absolute_url,
            description=f"HTML <{tag.name}> 资源",
        ))

    for track in soup.find_all("track"):
        if (track.get("kind") or "subtitles").lower() not in {"subtitles", "captions"}:
            continue
        src = track.get("src")
        absolute_url = _public_absolute_url(src, page_url) if src else None
        if not absolute_url or absolute_url in seen_subtitles:
            continue
        seen_subtitles.add(absolute_url)
        language = track.get("srclang") or "und"
        subtitles.append(SubtitleInfo(
            language=language,
            name=track.get("label") or language,
            url=absolute_url,
            extension=_extension_from_url_or_type(absolute_url, track.get("type")),
            source="html",
        ))
    if not formats and not subtitles:
        raise MediaExtractionError("未在静态 HTML 中找到公开的 video、audio、source 或 track 资源。")
    return MediaInfo(title=title, source_url=page_url, source_type="html", formats=formats, subtitles=subtitles, webpage_url=page_url)


def _public_absolute_url(value: str, page_url: str) -> str | None:
    absolute_url = urljoin(page_url, value)
    return absolute_url if urlparse(absolute_url).scheme in {"http", "https"} else None


def _extension_from_url_or_type(url: str, content_type: str | None) -> str | None:
    path = urlparse(url).path
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else None
    if suffix:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0])
        return guessed.lstrip(".") if guessed else None
    return None
