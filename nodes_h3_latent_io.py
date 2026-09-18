"""Save/load MiniMax H3 AV NestedTensor latents with output subfolder file I/O."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import comfy.nested_tensor
import comfy.utils
import folder_paths
import safetensors.torch
import torch
from comfy.cli_args import args

from .io_naming import output_path, resolve_output_stem

DEFAULT_SUBFOLDER = "latents/MiniMaxH3"
FORMAT_VERSION = 1
EXTENSION = ".h3latent"


def _clean_subfolder(subfolder: str = DEFAULT_SUBFOLDER) -> str:
    return (subfolder or DEFAULT_SUBFOLDER).strip().strip("/\\") or DEFAULT_SUBFOLDER


def _latents_dir(subfolder: str = DEFAULT_SUBFOLDER) -> Path:
    root = Path(folder_paths.get_output_directory())
    path = root / _clean_subfolder(subfolder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_nested_tensor(value: Any) -> bool:
    return isinstance(value, comfy.nested_tensor.NestedTensor) or getattr(
        value, "is_nested", False
    )


def _members(value: Any) -> list[torch.Tensor]:
    if _is_nested_tensor(value):
        tensors = list(value.unbind())
    elif isinstance(value, torch.Tensor):
        tensors = [value]
    else:
        raise TypeError(
            f'LATENT "samples" must be a torch.Tensor or NestedTensor, got {type(value).__name__}'
        )

    if not tensors:
        raise ValueError("Cannot save an empty NestedTensor latent")
    for index, tensor in enumerate(tensors):
        if not isinstance(tensor, torch.Tensor):
            raise TypeError(
                f"NestedTensor member {index} is {type(tensor).__name__}, expected torch.Tensor"
            )
    return tensors


def _list_h3_files(subfolder: str = DEFAULT_SUBFOLDER) -> list[str]:
    d = _latents_dir(subfolder)
    files = sorted(p.name for p in d.glob(f"*{EXTENSION}") if p.is_file())
    return files if files else ["(none)"]


def _load_h3_latent(path: Path) -> dict[str, Any]:
    data = safetensors.torch.load_file(str(path), device="cpu")

    if "tensor_count" not in data:
        raise ValueError("Not a MiniMax H3 AV latent file: missing tensor_count")
    count = int(data["tensor_count"].item())
    if count < 1:
        raise ValueError(f"Invalid MiniMax H3 AV latent tensor_count: {count}")

    tensors = []
    for index in range(count):
        key = f"latent_{index}"
        if key not in data:
            raise ValueError(f"Invalid MiniMax H3 AV latent file: missing {key}")
        tensors.append(data[key].float())

    samples = tensors[0] if count == 1 else comfy.nested_tensor.NestedTensor(tensors)
    return {"samples": samples}


class SaveH3AVLatent:
    """Save MiniMax H3 AV latents to output/{subfolder}/*.h3latent."""

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        try:
            from .routes_h3_latent_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "samples": ("LATENT",),
                "filename_prefix": ("STRING", {"default": "MiniMaxH3"}),
                "subfolder": ("STRING", {"default": DEFAULT_SUBFOLDER}),
            },
            "optional": {
                "basename": ("STRING", {"default": ""}),
                "video_filename": ("STRING", {"default": ""}),
                "timestamp": ("STRING", {"default": ""}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("samples",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "h3_latent_io"

    def save(
        self,
        samples,
        filename_prefix: str = "MiniMaxH3",
        subfolder: str = DEFAULT_SUBFOLDER,
        basename: str = "",
        video_filename: str = "",
        timestamp: str = "",
        prompt=None,
        extra_pnginfo=None,
    ):
        if "samples" not in samples:
            raise KeyError('LATENT input missing "samples"')

        tensors = _members(samples["samples"])
        sub = _clean_subfolder(subfolder)
        out_dir = _latents_dir(sub)
        stem, fixed = resolve_output_stem(
            basename=basename,
            video_filename=video_filename,
            timestamp=timestamp,
            filename_prefix=filename_prefix or "MiniMaxH3",
        )
        path = output_path(out_dir, stem, fixed, EXTENSION)
        output_filename = path.name
        output_path_str = str(path)
        subfolder_result = sub

        metadata = None
        if not args.disable_metadata:
            metadata = {
                "format": "minimax_h3_av_latent",
                "format_version": str(FORMAT_VERSION),
                "tensor_count": str(len(tensors)),
                "prompt": json.dumps(prompt) if prompt is not None else "",
            }
            if extra_pnginfo is not None:
                for key, value in extra_pnginfo.items():
                    metadata[key] = json.dumps(value)

        output = {
            "format_version": torch.tensor([FORMAT_VERSION], dtype=torch.int32),
            "tensor_count": torch.tensor([len(tensors)], dtype=torch.int32),
        }
        for index, tensor in enumerate(tensors):
            output[f"latent_{index}"] = tensor.detach().contiguous()

        comfy.utils.save_torch_file(output, output_path_str, metadata=metadata)
        print(f"[h3_latent_io] Saved H3 latent ({len(tensors)} member(s)) -> {output_path_str}")

        return {
            "ui": {
                "h3_latent_files": [
                    {
                        "filename": output_filename,
                        "subfolder": subfolder_result,
                        "type": "output",
                    }
                ]
            },
            "result": (samples,),
        }


class LoadH3AVLatent:
    """Load MiniMax H3 AV latents from output/{subfolder}/*.h3latent."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            from .routes_h3_latent_io import register_routes

            register_routes()
        except Exception:
            pass
        return {
            "required": {
                "latent_file": (_list_h3_files(),),
                "subfolder": ("STRING", {"default": DEFAULT_SUBFOLDER}),
            },
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("samples",)
    FUNCTION = "load"
    CATEGORY = "h3_latent_io"

    @classmethod
    def IS_CHANGED(cls, latent_file, subfolder):
        if not latent_file or latent_file == "(none)":
            return float("nan")
        path = _latents_dir(subfolder) / latent_file
        try:
            return os.path.getmtime(path)
        except OSError:
            return float("nan")

    def load(self, latent_file: str, subfolder: str = DEFAULT_SUBFOLDER):
        if not latent_file or latent_file == "(none)":
            raise FileNotFoundError(
                f"No .h3latent files in output/{subfolder or DEFAULT_SUBFOLDER}. "
                "Run Save MiniMax H3 AV Latent first."
            )
        path = _latents_dir(subfolder) / latent_file
        if not path.is_file():
            raise FileNotFoundError(f"H3 latent file not found: {path}")

        latent = _load_h3_latent(path)
        print(f"[h3_latent_io] Loaded H3 latent <- {path}")
        return (latent,)
