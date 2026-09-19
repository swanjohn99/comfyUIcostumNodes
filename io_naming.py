"""Shared output naming for mask and H3 latent save nodes."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from weakref import WeakKeyDictionary

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_VIDEO_EXTS = frozenset(
    {".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif", ".m4v", ".mpeg", ".mpg"}
)
_VIDEO_INPUT_KEYS = ("video", "file", "video_path", "filename", "path")
_VIDEO_LOAD_TYPES = frozenset(
    {
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "VHS_LoadVideoFFmpeg",
        "VHS_LoadVideoFFmpegPath",
        "LoadVideo",
        "LoadVideoPath",
        "LoadVideoUpload",
    }
)
_PROMPT_TIMESTAMPS: WeakKeyDictionary = WeakKeyDictionary()


def sanitize_stem(value: str) -> str:
    """Normalize a user/path value into a safe filename stem."""
    text = (value or "").strip()
    if not text:
        return ""
    text = Path(text).name
    if "." in text:
        text = Path(text).stem
    text = _INVALID_CHARS.sub("_", text)
    text = text.replace(" ", "_").strip("._")
    return text


def format_timestamp(timestamp: str = "") -> str:
    if timestamp and str(timestamp).strip():
        return _INVALID_CHARS.sub("_", str(timestamp).strip())
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_output_stem(
    *,
    basename: str = "",
    video_filename: str = "",
    timestamp: str = "",
    filename_prefix: str = "output",
) -> tuple[str, bool]:
    """
    Resolve the output filename stem.

    Returns (stem, fixed). When fixed is True, use stem as-is (no counter).
    When fixed is False, append _{#####} using the returned stem as prefix.
    """
    explicit = sanitize_stem(basename)
    if explicit:
        return explicit, True

    video = sanitize_stem(video_filename)
    if video:
        return f"{video}_{format_timestamp(timestamp)}", True

    prefix = sanitize_stem(filename_prefix) or "output"
    return prefix, False


def output_path(out_dir: Path, stem: str, fixed: bool, extension: str) -> Path:
    """Build the final output path for a save node."""
    if fixed:
        return out_dir / f"{stem}{extension}"
    return next_counter_path(out_dir, stem, extension)


def next_counter_path(out_dir: Path, prefix: str, extension: str) -> Path:
    counters: list[int] = []
    for path in out_dir.iterdir():
        if not path.is_file() or not path.name.endswith(extension):
            continue
        stem = path.stem
        marker = f"{prefix}_"
        if not stem.startswith(marker):
            continue
        tail = stem[len(marker) :]
        if tail.isdigit():
            counters.append(int(tail))
    counter = (max(counters) + 1) if counters else 1
    return out_dir / f"{prefix}_{counter:05d}{extension}"
