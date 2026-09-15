"""Application configuration and Windows output-path management."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(r"D:\MediaDownloader")


def find_portable_ffmpeg(output_dir: Path) -> Path | None:
    """Find a complete portable FFmpeg installation under the output directory."""
    root = output_dir / "FFmpeg"
    if not root.is_dir():
        return None
    for executable in root.rglob("ffmpeg.exe"):
        if (executable.parent / "ffprobe.exe").is_file():
            return executable
    return None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Runtime settings. Paths are created only when :meth:`ensure_directories` runs."""

    output_dir: Path = DEFAULT_OUTPUT_DIR
    request_timeout: int = 30
    max_filename_length: int = 120
    ffmpeg_path: str | None = None
    keep_failed_temp_files: bool = True
    browser_cookies: str | None = None

    def resolve_ffmpeg(self) -> str | None:
        if self.ffmpeg_path:
            return self.ffmpeg_path
        portable = find_portable_ffmpeg(self.output_dir)
        return str(portable) if portable else shutil.which("ffmpeg")

    @property
    def video_dir(self) -> Path:
        return self.output_dir / "Video"

    @property
    def audio_dir(self) -> Path:
        return self.output_dir / "Audio"

    @property
    def subtitle_dir(self) -> Path:
        return self.output_dir / "Subtitle"

    @property
    def temp_dir(self) -> Path:
        return self.output_dir / "Temp"

    @property
    def logs_dir(self) -> Path:
        return self.output_dir / "Logs"

    def ensure_directories(self) -> None:
        for directory in (
            self.video_dir,
            self.audio_dir,
            self.subtitle_dir,
            self.temp_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
