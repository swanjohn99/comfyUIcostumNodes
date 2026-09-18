import os
from pathlib import Path

import folder_paths
import torch

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
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "mask_io"

    def save(self, masks, filename_prefix: str, subfolder: str):
        mask = _normalize_mask(masks).cpu()
        sub = _clean_subfolder(subfolder)
        out_dir = _masks_dir(sub)
        prefix = (filename_prefix or "sam3_masks").strip() or "sam3_masks"
        # Sanitize path separators in prefix
        prefix = prefix.replace("/", "_").replace("\\", "_")

        existing = list(out_dir.glob(f"{prefix}_*.pt"))
        counters = []
        for p in existing:
            stem = p.stem  # prefix_00001
            if stem.startswith(prefix + "_"):
                tail = stem[len(prefix) + 1 :]
                if tail.isdigit():
                    counters.append(int(tail))
        counter = (max(counters) + 1) if counters else 1

        path = out_dir / f"{prefix}_{counter:05d}.pt"
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
    """Load a previously saved MASK .pt file from output/masks."""

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
            },
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "load"
    CATEGORY = "mask_io"

    @classmethod
    def IS_CHANGED(cls, mask_file, subfolder):
        if not mask_file or mask_file == "(none)":
            return float("nan")
        path = _masks_dir(subfolder) / mask_file
        try:
            return os.path.getmtime(path)
        except OSError:
            return float("nan")

    def load(self, mask_file: str, subfolder: str):
        if not mask_file or mask_file == "(none)":
            raise FileNotFoundError(
                f"No .pt mask files in output/{subfolder or 'masks'}. "
                "Run Save Mask Tensor first."
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
