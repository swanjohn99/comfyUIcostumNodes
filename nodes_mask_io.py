import os
import shutil
from pathlib import Path

import folder_paths
import torch

_ROOT_CHOICES = ("output", "input")
_DEST_CHOICES = ("output", "input", "both")


def _clean_subfolder(subfolder: str = "masks") -> str:
    return (subfolder or "masks").strip().strip("/\\") or "masks"


def _root_dir(root: str = "output") -> Path:
    root = (root or "output").strip().lower()
    if root == "input":
        return Path(folder_paths.get_input_directory())
    return Path(folder_paths.get_output_directory())


def _masks_dir(subfolder: str = "masks", root: str = "output") -> Path:
    path = _root_dir(root) / _clean_subfolder(subfolder)
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


def _next_path(out_dir: Path, prefix: str) -> Path:
    existing = list(out_dir.glob(f"{prefix}_*.pt"))
    counters = []
    for p in existing:
        stem = p.stem  # prefix_00001
        if stem.startswith(prefix + "_"):
            tail = stem[len(prefix) + 1 :]
            if tail.isdigit():
                counters.append(int(tail))
    counter = (max(counters) + 1) if counters else 1
    return out_dir / f"{prefix}_{counter:05d}.pt"


def _list_mask_files(subfolder: str = "masks", root: str = "output") -> list[str]:
    d = _masks_dir(subfolder, root)
    files = sorted(p.name for p in d.glob("*.pt") if p.is_file())
    return files if files else ["(none)"]


class SaveMaskTensor:
    """Save a ComfyUI MASK tensor to a .pt file under output and/or input."""

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
                "destination": (_DEST_CHOICES, {"default": "output"}),
            },
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "mask_io"

    def save(self, masks, filename_prefix: str, subfolder: str, destination: str):
        mask = _normalize_mask(masks).cpu()
        sub = _clean_subfolder(subfolder)
        dest = (destination or "output").strip().lower()
        if dest not in _DEST_CHOICES:
            dest = "output"

        prefix = (filename_prefix or "sam3_masks").strip() or "sam3_masks"
        prefix = prefix.replace("/", "_").replace("\\", "_")

        ui_files = []
        primary = None

        if dest in ("output", "both"):
            out_path = _next_path(_masks_dir(sub, "output"), prefix)
            torch.save(mask, out_path)
            print(f"[mask_io] Saved MASK {tuple(mask.shape)} -> {out_path}")
            ui_files.append(
                {"filename": out_path.name, "subfolder": sub, "type": "output"}
            )
            primary = out_path

        if dest in ("input", "both"):
            in_dir = _masks_dir(sub, "input")
            if dest == "both" and primary is not None:
                in_path = in_dir / primary.name
                shutil.copy2(primary, in_path)
            else:
                in_path = _next_path(in_dir, prefix)
                torch.save(mask, in_path)
            print(f"[mask_io] Saved MASK {tuple(mask.shape)} -> {in_path}")
            ui_files.append(
                {"filename": in_path.name, "subfolder": sub, "type": "input"}
            )

        return {
            "ui": {"mask_files": ui_files},
            "result": (mask,),
        }


class LoadMaskTensor:
    """Load a previously saved MASK .pt file from output/ or input/ masks."""

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
                "source": (_ROOT_CHOICES, {"default": "output"}),
            },
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "load"
    CATEGORY = "mask_io"

    @classmethod
    def IS_CHANGED(cls, mask_file, subfolder, source="output"):
        if not mask_file or mask_file == "(none)":
            return float("nan")
        path = _masks_dir(subfolder, source) / mask_file
        try:
            return os.path.getmtime(path)
        except OSError:
            return float("nan")

    def load(self, mask_file: str, subfolder: str, source: str = "output"):
        root = (source or "output").strip().lower()
        if root not in _ROOT_CHOICES:
            root = "output"
        if not mask_file or mask_file == "(none)":
            raise FileNotFoundError(
                f"No .pt mask files in {root}/{subfolder or 'masks'}. "
                "Run Save Mask Tensor first."
            )
        path = _masks_dir(subfolder, root) / mask_file
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
