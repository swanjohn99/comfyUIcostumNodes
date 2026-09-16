#!/usr/bin/env python3
"""Input-image constraints for image-to-video models."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

Size = tuple[int, int]

FORMAT_ALIASES = {"jpeg": "jpg"}
PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


def norm_format(fmt: str | None) -> str:
    fmt = (fmt or "").lower().lstrip(".")
    return FORMAT_ALIASES.get(fmt, fmt)


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    # Exact allowed output sizes (WxH). Image is cropped to nearest aspect then resized.
    sizes: tuple[Size, ...] = ()
    # Area bucket (WxH). Input aspect is kept, dims derived from the area, snapped to multiple_of.
    area: Size | None = None
    multiple_of: int = 1
    min_side: int = 0
    max_side: int = 0  # 0 = unlimited
    aspect_range: tuple[float, float] = (0.0, 0.0)  # w/h, (0,0) = unlimited
    formats: tuple[str, ...] = ("jpg", "png")
    default_format: str = "jpg"
    max_bytes: int = 0  # 0 = unlimited
    allow_alpha: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ----- geometry -----

    def target_size(self, width: int, height: int) -> Size | None:
        """Return the size the image should end up at, or None if only constraints apply."""
        if self.sizes:
            return nearest_by_aspect(width, height, self.sizes)
        if self.area:
            return size_for_area(width, height, self.area, self.multiple_of)
        return None

    def target_aspect(self, width: int, height: int) -> float:
        """Aspect (w/h) the image must have before resizing."""
        size = self.target_size(width, height)
        if size:
            return size[0] / size[1]
        aspect = width / height
        lo, hi = self.aspect_range
        if lo and aspect < lo:
            return lo
        if hi and aspect > hi:
            return hi
        return aspect

    # ----- validation -----

    def violations(
        self,
        width: int,
        height: int,
        fmt: str | None = None,
        nbytes: int = 0,
        has_alpha: bool = False,
    ) -> list[str]:
        problems: list[str] = []
        if self.sizes and (width, height) not in self.sizes:
            problems.append(f"size {width}x{height} not in allowed set")
        if self.multiple_of > 1 and (width % self.multiple_of or height % self.multiple_of):
            problems.append(f"dims must be multiples of {self.multiple_of}")
        if self.area:
            budget = self.area[0] * self.area[1]
            if abs(width * height - budget) / budget > 0.15:
                problems.append(f"area {width * height / 1e6:.2f}MP vs bucket {budget / 1e6:.2f}MP")
        if self.min_side and min(width, height) < self.min_side:
            problems.append(f"min side {min(width, height)} < {self.min_side}")
        if self.max_side and max(width, height) > self.max_side:
            problems.append(f"max side {max(width, height)} > {self.max_side}")
        lo, hi = self.aspect_range
        aspect = width / height
        if lo and aspect < lo - 1e-6:
            problems.append(f"aspect {aspect:.3f} < {lo}")
        if hi and aspect > hi + 1e-6:
            problems.append(f"aspect {aspect:.3f} > {hi}")
        if fmt:
            fmt = norm_format(fmt)
            if fmt not in self.formats:
                problems.append(f"format {fmt} not in {'/'.join(self.formats)}")
        if self.max_bytes and nbytes > self.max_bytes:
            problems.append(f"file {nbytes / 1e6:.2f}MB > {self.max_bytes / 1e6:.2f}MB")
        if has_alpha and not self.allow_alpha:
            problems.append("alpha channel not allowed")
        return problems

    def to_dict(self) -> dict:
        return asdict(self)


# ----- helpers -----


def snap(value: float, multiple: int) -> int:
    if multiple <= 1:
        return max(1, int(round(value)))
    return max(multiple, int(value // multiple) * multiple)


def aspect_distance(a: float, b: float) -> float:
    return abs(math.log(a) - math.log(b))


def nearest_by_aspect(width: int, height: int, sizes: tuple[Size, ...]) -> Size:
    aspect = width / height
    return min(sizes, key=lambda s: aspect_distance(aspect, s[0] / s[1]))


def size_for_area(width: int, height: int, area: Size, multiple: int) -> Size:
    """Keep input aspect, hit the area budget, snap both dims down to `multiple`."""
    budget = area[0] * area[1]
    scale = math.sqrt(budget / (width * height))
    return snap(width * scale, multiple), snap(height * scale, multiple)


# ----- table -----

MB = 1_000_000

PRESETS: dict[str, Preset] = {
    "wan-480p": Preset(
        name="wan-480p",
        description="Wan 2.1/2.2 I2V, 480p bucket (832x480 area), dims %16, aspect follows input",
        area=(832, 480),
        multiple_of=16,
        formats=("jpg", "png", "webp"),
        default_format="png",
        notes=("Native buckets: 832x480 / 480x832.", "Frames: 4n+1."),
    ),
    "wan-720p": Preset(
        name="wan-720p",
        description="Wan 2.2 I2V-A14B, 720p bucket (1280x720 area), dims %16, aspect follows input",
        area=(1280, 720),
        multiple_of=16,
        formats=("jpg", "png", "webp"),
        default_format="png",
        notes=("Native buckets: 1280x720 / 720x1280.", "TI2V-5B uses 1280x704."),
    ),
    "wan-5b-720p": Preset(
        name="wan-5b-720p",
        description="Wan 2.2 TI2V-5B, 1280x704 area, dims %32 (VAE 16 x patch 2)",
        area=(1280, 704),
        multiple_of=32,
        formats=("jpg", "png", "webp"),
        default_format="png",
    ),
    "ltx-base": Preset(
        name="ltx-base",
        description="LTX-2 base pass (960x544 area), dims %32",
        area=(960, 544),
        multiple_of=32,
        formats=("jpg", "png", "webp"),
        default_format="png",
        notes=("Frames: 8n+1.", "Two-stage: base then x2 upscale to 1920x1088."),
    ),
    "ltx-720p": Preset(
        name="ltx-720p",
        description="LTX-2 ~720p (1280x704 area), dims %32",
        area=(1280, 704),
        multiple_of=32,
        formats=("jpg", "png", "webp"),
        default_format="png",
    ),
    "ltx-1080p": Preset(
        name="ltx-1080p",
        description="LTX-2 ~1080p (1920x1088 area), dims %32",
        area=(1920, 1088),
        multiple_of=32,
        formats=("jpg", "png", "webp"),
        default_format="png",
    ),
    "kling": Preset(
        name="kling",
        description="Kling 2.x/3.0 first frame: >=300px sides, <=8000px, aspect 1:2.5..2.5:1, <=10MB, no alpha",
        min_side=300,
        max_side=8000,
        aspect_range=(0.4, 2.5),
        formats=("jpg", "png"),
        default_format="jpg",
        max_bytes=10 * MB,
        allow_alpha=False,
        notes=("Output aspect inherits first frame.", "Needs public URL upload."),
    ),
    "runway": Preset(
        name="runway",
        description="Runway Gen-4/4.5: exact ratios, JPG/PNG/WebP, <=3.3MB for data URI (5MB encoded)",
        sizes=((1280, 720), (720, 1280), (960, 960), (1104, 832), (832, 1104), (1584, 672)),
        formats=("jpg", "png", "webp"),
        default_format="jpg",
        max_bytes=int(3.3 * MB),
        notes=("URL inputs allow 16MB; data URI 5MB encoded.", "GIF unsupported."),
    ),
    "veo3": Preset(
        name="veo3",
        description="Veo 3 I2V 720p: 16:9 or 9:16, JPG/PNG, <=7MB inline",
        sizes=((1280, 720), (720, 1280)),
        formats=("jpg", "png"),
        default_format="jpg",
        max_bytes=7 * MB,
        allow_alpha=False,
    ),
    "veo3-1080p": Preset(
        name="veo3-1080p",
        description="Veo 3 I2V 1080p: 16:9 or 9:16, JPG/PNG, <=7MB inline",
        sizes=((1920, 1080), (1080, 1920)),
        formats=("jpg", "png"),
        default_format="jpg",
        max_bytes=7 * MB,
        allow_alpha=False,
    ),
}


def load_preset_file(path: Path) -> Preset:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("name", path.stem)
    data.setdefault("description", f"custom preset from {path.name}")
    for key in ("sizes",):
        if key in data:
            data[key] = tuple(tuple(int(v) for v in s) for s in data[key])
    if data.get("area"):
        data["area"] = tuple(int(v) for v in data["area"])
    if "aspect_range" in data:
        data["aspect_range"] = tuple(float(v) for v in data["aspect_range"])
    for key in ("formats", "notes"):
        if key in data:
            data[key] = tuple(data[key])
    if "max_bytes" in data and isinstance(data["max_bytes"], float):
        data["max_bytes"] = int(data["max_bytes"])
    unknown = set(data) - set(Preset.__dataclass_fields__)
    if unknown:
        raise SystemExit(f"unknown preset fields in {path}: {sorted(unknown)}")
    return Preset(**data)


def get_preset(name: str | None, preset_file: Path | None = None) -> Preset | None:
    if preset_file:
        return load_preset_file(preset_file)
    if not name:
        return None
    try:
        return PRESETS[name]
    except KeyError:
        raise SystemExit(f"unknown preset {name!r}; options: {', '.join(PRESETS)}")
