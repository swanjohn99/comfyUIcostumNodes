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


def shared_timestamp(prompt, timestamp: str = "") -> str:
    """Reuse one auto timestamp for every save in the same prompt run."""
    if timestamp and str(timestamp).strip():
        return format_timestamp(timestamp)
    if isinstance(prompt, dict):
        try:
            return _PROMPT_TIMESTAMPS.setdefault(prompt, format_timestamp(""))
        except TypeError:
            pass
    return format_timestamp("")


def _is_video_load_type(class_type: str) -> bool:
    text = class_type or ""
    if text in _VIDEO_LOAD_TYPES:
        return True
    if re.search(r"Save|Combine|Write|Preview|Encode", text, re.I):
        return False
    return "LoadVideo" in text


def _looks_like_video_filename(value: str) -> bool:
    name = Path(str(value).strip()).name
    return Path(name).suffix.lower() in _VIDEO_EXTS


def _filename_from_node(node: dict) -> str:
    inputs = node.get("inputs") or {}
    if not isinstance(inputs, dict):
        return ""
    for key in _VIDEO_INPUT_KEYS:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in inputs.values():
        if isinstance(value, str) and _looks_like_video_filename(value):
            return value.strip()
    return ""


def _prompt_nodes(prompt) -> dict[str, dict]:
    if not isinstance(prompt, dict):
        return {}
    return {str(key): value for key, value in prompt.items() if isinstance(value, dict)}


def _walk_ancestors(nodes: dict[str, dict], start_id: str):
    seen: set[str] = set()
    stack = [str(start_id)]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = nodes.get(node_id)
        if not node:
            continue
        yield node_id, node
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if isinstance(value, list) and value:
                stack.append(str(value[0]))


def extract_video_filename_from_prompt(prompt, unique_id: str = "") -> str:
    """Read the selected media filename from a Load Video node in the prompt."""
    nodes = _prompt_nodes(prompt)
    if not nodes:
        return ""

    if unique_id:
        for node_id, node in _walk_ancestors(nodes, str(unique_id)):
            if node_id == str(unique_id):
                continue
            if _is_video_load_type(str(node.get("class_type") or "")):
                name = _filename_from_node(node)
                if name:
                    return name

    found: list[str] = []
    for node in nodes.values():
        if not _is_video_load_type(str(node.get("class_type") or "")):
            continue
        name = _filename_from_node(node)
        if name:
            found.append(name)
    unique = list(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 else ""


def resolve_save_stem(
    *,
    basename: str = "",
    video_filename: str = "",
    timestamp: str = "",
    filename_prefix: str = "output",
    prompt=None,
    unique_id: str = "",
) -> tuple[str, bool]:
    """Resolve stem, filling video filename from the prompt when omitted."""
    if not sanitize_stem(basename) and not sanitize_stem(video_filename):
        video_filename = extract_video_filename_from_prompt(prompt, unique_id)
    return resolve_output_stem(
        basename=basename,
        video_filename=video_filename,
        timestamp=shared_timestamp(prompt, timestamp),
        filename_prefix=filename_prefix,
    )


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
