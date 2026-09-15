"""Windows-safe, collision-free filenames."""

from __future__ import annotations

import re
from pathlib import Path


_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_filename(value: str, max_length: int = 120, fallback: str = "untitled") -> str:
    """Return a Unicode filename stem safe on Windows (without an extension)."""
    if max_length < 1:
        raise ValueError("max_length 必须大于 0")
    cleaned = _INVALID_CHARS.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper().split(".")[0] in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or fallback


def unique_path(directory: Path, stem: str, suffix: str, max_stem_length: int = 120) -> Path:
    """Build a non-existing destination path without changing existing files."""
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    safe_stem = sanitize_filename(stem, max_stem_length)
    candidate = directory / f"{safe_stem}{suffix}"
    counter = 1
    while candidate.exists():
        marker = f" ({counter})"
        available = max(1, max_stem_length - len(marker))
        candidate = directory / f"{safe_stem[:available].rstrip(' .')}{marker}{suffix}"
        counter += 1
    return candidate
