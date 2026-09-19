"""Build a shared basename for mask/latent save nodes."""
from __future__ import annotations

from .io_naming import resolve_save_stem


class BuildMediaIoBasename:
    """Emit {video_stem}_{timestamp} once for wiring into save nodes."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_filename": ("STRING", {"default": ""}),
            },
            "optional": {
                "timestamp": ("STRING", {"default": ""}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("basename",)
    FUNCTION = "build"
    CATEGORY = "mask_io"

    def build(self, video_filename: str, timestamp: str = ""):
        stem, _ = resolve_output_stem(
            video_filename=video_filename,
            timestamp=timestamp,
        )
        return (stem,)
