"""Source-independent media data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


MediaKind = Literal["video", "audio", "combined"]
SourceType = Literal["ytdlp", "html"]
ProgressCallback = Callable[[str, float | None], None]


@dataclass(slots=True, frozen=True)
class MediaFormat:
    format_id: str
    kind: MediaKind
    extension: str | None = None
    url: str | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    filesize: int | None = None
    description: str | None = None

    @property
    def label(self) -> str:
        details: list[str] = []
        if self.extension:
            details.append(self.extension)
        if self.width and self.height:
            details.append(f"{self.width}x{self.height}")
        if self.filesize is not None:
            details.append(format_filesize(self.filesize))
        return ", ".join(details) or "信息未知"


@dataclass(slots=True, frozen=True)
class SubtitleInfo:
    language: str
    name: str
    url: str | None = None
    extension: str | None = None
    source: SourceType = "html"


@dataclass(slots=True)
class MediaInfo:
    title: str
    source_url: str
    source_type: SourceType
    formats: list[MediaFormat] = field(default_factory=list)
    subtitles: list[SubtitleInfo] = field(default_factory=list)
    webpage_url: str | None = None

    @property
    def video_formats(self) -> list[MediaFormat]:
        return [item for item in self.formats if item.kind in {"video", "combined"}]

    @property
    def audio_formats(self) -> list[MediaFormat]:
        return [item for item in self.formats if item.kind in {"audio", "combined"}]


@dataclass(slots=True, frozen=True)
class DownloadResult:
    path: Path
    kind: Literal["video", "audio", "subtitle"]
    merged_or_converted: bool = False


def format_filesize(value: int | None) -> str:
    if value is None:
        return "未知"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "未知"
