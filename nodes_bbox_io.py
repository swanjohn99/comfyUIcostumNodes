import json
import os
from pathlib import Path

import folder_paths

from .io_naming import output_path, resolve_save_stem

# .cbbox only. Not .pt (Comfy model scanner) or .json (workflows / other loaders).
BBOX_EXTENSION = ".cbbox"
FORMAT_NAME = "mvex_bboxes"
FORMAT_VERSION = 1
DEFAULT_SUBFOLDER = "bboxes"
BOX_KEYS = ("x", "y", "width", "height")
OPTIONAL_META_KEYS = ("source_width", "source_height")


def _clean_subfolder(subfolder: str = DEFAULT_SUBFOLDER) -> str:
    return (subfolder or DEFAULT_SUBFOLDER).strip().strip("/\\") or DEFAULT_SUBFOLDER


def _bboxes_dir(subfolder: str = DEFAULT_SUBFOLDER) -> Path:
    root = Path(folder_paths.get_output_directory())
    path = root / _clean_subfolder(subfolder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_bbox_filename(name: str) -> bool:
    return bool(name) and Path(name).suffix.lower() == BBOX_EXTENSION


def _iter_bbox_paths(directory: Path):
    for path in directory.glob(f"*{BBOX_EXTENSION}"):
        if _is_bbox_filename(path.name):
            yield path


def _list_bbox_files(subfolder: str = DEFAULT_SUBFOLDER) -> list[str]:
    d = _bboxes_dir(subfolder)
    files = sorted(
        p.name
        for p in _iter_bbox_paths(d)
        if p.is_file() and _is_bbox_filename(p.name)
    )
    return files if files else ["(none)"]


def _num(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Bounding box coordinate must be a number, got {type(value).__name__}")
    return value


def _box_dict(obj) -> dict:
    if not isinstance(obj, dict):
        raise TypeError(f"Bounding box must be a dict, got {type(obj).__name__}")
    missing = [key for key in BOX_KEYS if key not in obj]
    if missing:
        raise ValueError(f"Bounding box missing keys: {missing}")
    return {
        "x": _num(obj["x"]),
        "y": _num(obj["y"]),
        "width": _num(obj["width"]),
        "height": _num(obj["height"]),
    }


def _is_box_dict(obj) -> bool:
    return isinstance(obj, dict) and all(key in obj for key in BOX_KEYS)


def _normalize_frame(frame) -> list[dict]:
    if _is_box_dict(frame):
        return [_box_dict(frame)]
    if isinstance(frame, (list, tuple)):
        return [_box_dict(box) for box in frame]
    raise TypeError(
        "Each frame must be a box dict or a list of box dicts, "
        f"got {type(frame).__name__}"
    )


def _extract_optional_meta(obj) -> dict:
    meta = {}
    if not isinstance(obj, dict) or _is_box_dict(obj):
        return meta
    for key in OPTIONAL_META_KEYS:
        if key not in obj:
            continue
        try:
            meta[key] = int(_num(obj[key]))
        except (TypeError, ValueError):
            continue
    return meta


def _normalize_bboxes(bboxes):
    """Return (payload, frame_count, optional_meta).

    payload is either one box dict (broadcast) or a list of per-frame box lists.
    """
    meta = _extract_optional_meta(bboxes)
    if isinstance(bboxes, dict) and "bboxes" in bboxes and not _is_box_dict(bboxes):
        payload, frame_count, inner_meta = _normalize_bboxes(bboxes["bboxes"])
        return payload, frame_count, {**meta, **inner_meta}

    if _is_box_dict(bboxes):
        return _box_dict(bboxes), 1, meta

    if not isinstance(bboxes, (list, tuple)):
        raise TypeError(
            "BOUNDING_BOX must be a box dict or a list of frames, "
            f"got {type(bboxes).__name__}"
        )

    frames = [_normalize_frame(frame) for frame in bboxes]
    return frames, len(frames), meta


def _slice_frames(frames: list, skip_first_frames: int, frame_load_cap: int, select_every_nth: int):
    n = len(frames)
    if skip_first_frames >= n:
        if skip_first_frames == 0:
            return []
        raise ValueError(f"skip_first_frames {skip_first_frames} >= {n} frames")
    end = n if frame_load_cap <= 0 else min(n, skip_first_frames + frame_load_cap)
    return frames[skip_first_frames:end:select_every_nth]


def _load_payload(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".pt":
        raise ValueError(
            f"Rejected {path.name}: .pt is not a bbox file "
            f"(expected {BBOX_EXTENSION} JSON)"
        )
    if not _is_bbox_filename(path.name):
        raise ValueError(
            f"Unsupported bbox file extension: {path.name!r} (expected {BBOX_EXTENSION})"
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise TypeError(f"Expected object in {path.name}, got {type(data).__name__}")
    if data.get("format") != FORMAT_NAME:
        raise ValueError(
            f"Unexpected format in {path.name}: {data.get('format')!r} "
            f"(expected {FORMAT_NAME!r})"
        )
    if "bboxes" not in data:
        raise ValueError(f"Missing 'bboxes' in {path.name}")

    payload, frame_count, _meta = _normalize_bboxes(data["bboxes"])
    return payload, frame_count


class SaveBoundingBoxes:
    """Save a ComfyUI BOUNDING_BOX payload to a .cbbox JSON file under output/bboxes."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            from .routes_bbox_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "bboxes": ("BOUNDING_BOX",),
                "filename_prefix": ("STRING", {"default": "bboxes"}),
                "subfolder": ("STRING", {"default": DEFAULT_SUBFOLDER}),
            },
            "optional": {
                "basename": ("STRING", {"default": ""}),
                "video_filename": ("STRING", {"default": ""}),
                "timestamp": ("STRING", {"default": ""}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("BOUNDING_BOX",)
    RETURN_NAMES = ("bboxes",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "bbox_io"

    def save(
        self,
        bboxes,
        filename_prefix: str,
        subfolder: str,
        basename: str = "",
        video_filename: str = "",
        timestamp: str = "",
        prompt=None,
        unique_id: str = "",
    ):
        payload, frame_count, meta = _normalize_bboxes(bboxes)
        sub = _clean_subfolder(subfolder)
        out_dir = _bboxes_dir(sub)
        stem, fixed = resolve_save_stem(
            basename=basename,
            video_filename=video_filename,
            timestamp=timestamp,
            filename_prefix=filename_prefix or "bboxes",
            prompt=prompt,
            unique_id=unique_id,
        )
        path = output_path(out_dir, stem, fixed, BBOX_EXTENSION)
        if path.suffix.lower() != BBOX_EXTENSION:
            raise ValueError(f"Refusing non-{BBOX_EXTENSION} path: {path}")

        document = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "frame_count": frame_count,
            "bboxes": payload,
        }
        for key, value in meta.items():
            document[key] = value

        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        print(f"[bbox_io] Saved BOUNDING_BOX frames={frame_count} -> {path}")
        return {
            "ui": {
                "bbox_files": [
                    {
                        "filename": path.name,
                        "subfolder": sub,
                        "type": "output",
                    }
                ]
            },
            "result": (payload,),
        }


class LoadBoundingBoxes:
    """Load a previously saved .cbbox JSON file from output/bboxes."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            from .routes_bbox_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "bbox_file": (_list_bbox_files(),),
                "subfolder": ("STRING", {"default": DEFAULT_SUBFOLDER}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0, "max": 1_000_000}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": 1_000_000}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 100}),
            },
        }

    RETURN_TYPES = ("BOUNDING_BOX", "INT")
    RETURN_NAMES = ("bboxes", "frame_count")
    FUNCTION = "load"
    CATEGORY = "bbox_io"

    @classmethod
    def VALIDATE_INPUTS(cls, bbox_file, subfolder=DEFAULT_SUBFOLDER):
        if not bbox_file or bbox_file == "(none)":
            return f"No bbox files in output/{subfolder or DEFAULT_SUBFOLDER}."
        if bbox_file.lower().endswith(".pt"):
            return (
                f"Rejected {bbox_file!r}: .pt is not supported "
                f"(expected {BBOX_EXTENSION})"
            )
        if not _is_bbox_filename(bbox_file):
            return f"Unsupported bbox file extension: {bbox_file!r} (expected {BBOX_EXTENSION})"
        path = _bboxes_dir(subfolder) / bbox_file
        if not path.is_file():
            return f"BBox file not found: {path}"
        return True

    @classmethod
    def IS_CHANGED(cls, bbox_file, subfolder, **kwargs):
        if not bbox_file or bbox_file == "(none)":
            return float("nan")
        path = _bboxes_dir(subfolder) / bbox_file
        try:
            return os.path.getmtime(path)
        except OSError:
            return float("nan")

    def load(
        self,
        bbox_file: str,
        subfolder: str,
        skip_first_frames: int = 0,
        frame_load_cap: int = 0,
        select_every_nth: int = 1,
    ):
        if not bbox_file or bbox_file == "(none)":
            raise FileNotFoundError(
                f"No bbox files in output/{subfolder or DEFAULT_SUBFOLDER}. "
                "Run Save Bounding Boxes first."
            )
        if bbox_file.lower().endswith(".pt"):
            raise ValueError(
                f"Rejected {bbox_file!r}: .pt is not supported "
                f"(expected {BBOX_EXTENSION})"
            )
        if not _is_bbox_filename(bbox_file):
            raise ValueError(
                f"Unsupported bbox file extension: {bbox_file!r} (expected {BBOX_EXTENSION})"
            )
        path = _bboxes_dir(subfolder) / bbox_file
        if not path.is_file():
            raise FileNotFoundError(f"BBox file not found: {path}")

        payload, frame_count = _load_payload(path)
        if isinstance(payload, list):
            payload = _slice_frames(
                payload, skip_first_frames, frame_load_cap, select_every_nth
            )
            frame_count = len(payload)
        elif skip_first_frames > 0:
            raise ValueError("skip_first_frames is not valid for a broadcast bounding box")

        print(f"[bbox_io] Loaded BOUNDING_BOX frames={frame_count} <- {path}")
        return (payload, frame_count)
