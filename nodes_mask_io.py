import os
from pathlib import Path

import folder_paths
import torch

from .io_naming import output_path, resolve_save_stem

# Avoid .pt — ComfyUI's missing-model scanner treats it as a model file.
MASK_EXTENSION = ".cmask"
LEGACY_MASK_EXTENSION = ".pt"
MASK_EXTENSIONS = (MASK_EXTENSION, LEGACY_MASK_EXTENSION)


def _clean_subfolder(subfolder: str = "masks") -> str:
    return (subfolder or "masks").strip().strip("/\\") or "masks"


def _masks_dir(subfolder: str = "masks") -> Path:
    root = Path(folder_paths.get_output_directory())
    path = root / _clean_subfolder(subfolder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_mask(mask: torch.Tensor) -> torch.Tensor:
    """Force ComfyUI MASK shape [B, H, W]."""
    t = mask.detach()
    if t.ndim == 2:
        t = t.unsqueeze(0)
    elif t.ndim == 4:
        # [B, 1, H, W] or [B, H, W, 1]
        if t.shape[1] == 1:
            t = t[:, 0]
        elif t.shape[-1] == 1:
            t = t[..., 0]
        else:
            raise ValueError(f"Unsupported MASK shape: {tuple(t.shape)}")
    elif t.ndim != 3:
        raise ValueError(f"Unsupported MASK shape: {tuple(t.shape)}")
    return t.float().contiguous()


def _is_mask_filename(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in MASK_EXTENSIONS)


def _iter_mask_paths(directory: Path):
    for ext in MASK_EXTENSIONS:
        yield from directory.glob(f"*{ext}")


def _list_mask_files(subfolder: str = "masks") -> list[str]:
    d = _masks_dir(subfolder)
    files = sorted(p.name for p in _iter_mask_paths(d) if p.is_file())
    return files if files else ["(none)"]


class SaveMaskTensor:
    """Save a ComfyUI MASK tensor to a .cmask file under output/masks."""

    @classmethod
    def INPUT_TYPES(cls):
        # Retry route registration if PromptServer was not ready at import.
        try:
            from .routes_mask_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "masks": ("MASK",),
                "filename_prefix": ("STRING", {"default": "sam3_masks"}),
                "subfolder": ("STRING", {"default": "masks"}),
            },
            "optional": {
                "basename": ("STRING", {"default": ""}),
                "video_filename": ("STRING", {"default": ""}),
                "timestamp": ("STRING", {"default": ""}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "mask_io"

    def save(
        self,
        masks,
        filename_prefix: str,
        subfolder: str,
        basename: str = "",
        video_filename: str = "",
        timestamp: str = "",
        prompt=None,
        unique_id: str = "",
    ):
        mask = _normalize_mask(masks).cpu()
        sub = _clean_subfolder(subfolder)
        out_dir = _masks_dir(sub)
        stem, fixed = resolve_save_stem(
            basename=basename,
            video_filename=video_filename,
            timestamp=timestamp,
            filename_prefix=filename_prefix or "sam3_masks",
            prompt=prompt,
            unique_id=unique_id,
        )
        path = output_path(out_dir, stem, fixed, MASK_EXTENSION)
        torch.save(mask, path)
        print(f"[mask_io] Saved MASK {tuple(mask.shape)} -> {path}")
        return {
            "ui": {
                "mask_files": [
                    {
                        "filename": path.name,
                        "subfolder": sub,
                        "type": "output",
                    }
                ]
            },
            "result": (mask,),
        }


class LoadMaskTensor:
    """Load a previously saved MASK .cmask (or legacy .pt) file from output/masks."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            from .routes_mask_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "mask_file": (_list_mask_files(),),
                "subfolder": ("STRING", {"default": "masks"}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0, "max": 1_000_000}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": 1_000_000}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 100}),
            },
        }

    RETURN_TYPES = ("MASK", "INT")
    RETURN_NAMES = ("masks", "frame_count")
    FUNCTION = "load"
    CATEGORY = "mask_io"

    @classmethod
    def IS_CHANGED(cls, mask_file, subfolder, **kwargs):
        if not mask_file or mask_file == "(none)":
            return float("nan")
        path = _masks_dir(subfolder) / mask_file
        try:
            return os.path.getmtime(path)
        except OSError:
            return float("nan")

    def load(
        self,
        mask_file: str,
        subfolder: str,
        skip_first_frames: int = 0,
        frame_load_cap: int = 0,
        select_every_nth: int = 1,
    ):
        if not mask_file or mask_file == "(none)":
            raise FileNotFoundError(
                f"No mask files in output/{subfolder or 'masks'}. "
                "Run Save Mask Tensor first."
            )
        if not _is_mask_filename(mask_file):
            raise ValueError(
                f"Unsupported mask file extension: {mask_file!r} "
                f"(expected {MASK_EXTENSION} or legacy {LEGACY_MASK_EXTENSION})"
            )
        path = _masks_dir(subfolder) / mask_file
        if not path.is_file():
            raise FileNotFoundError(f"Mask file not found: {path}")

        try:
            data = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            data = torch.load(path, map_location="cpu")

        if isinstance(data, dict):
            if "masks" in data:
                data = data["masks"]
            elif "mask" in data:
                data = data["mask"]
            else:
                raise ValueError(
                    f"Unexpected dict keys in {path.name}: {list(data.keys())}"
                )
        if not isinstance(data, torch.Tensor):
            raise TypeError(f"Expected tensor in {path.name}, got {type(data)}")

        mask = _normalize_mask(data).cpu()
        print(f"[mask_io] Loaded MASK {tuple(mask.shape)} <- {path}")
        return (mask,)
