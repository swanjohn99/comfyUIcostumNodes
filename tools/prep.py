#!/usr/bin/env python3
"""Prepare input images for image-to-video models (Wan, Kling, Runway, Veo, LTX)."""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageOps

from presets import PIL_FORMAT, PRESETS, Preset, get_preset, norm_format, size_for_area, snap

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("prepped")
DEFAULT_YOLO = REPO_ROOT / "yolov8n.pt"
MANIFEST_NAME = "prep_manifest.json"
ANCHORS = {
    "center": (0.5, 0.5),
    "top": (0.5, 0.0),
    "bottom": (0.5, 1.0),
    "left": (0.0, 0.5),
    "right": (1.0, 0.5),
}
ASPECT_TOL = 0.005
MB = 1_000_000

Image.MAX_IMAGE_PIXELS = None
_YOLO_MODEL = None


# --------------------------------------------------------------------------- job / ops


@dataclass
class Job:
    src: Path
    ops: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def op(self, name: str, **params) -> None:
        self.ops.append({"op": name, **params})

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# --------------------------------------------------------------------------- loading / info


def load_image(path: Path, job: Job | None = None) -> tuple[Image.Image, str]:
    """Open, apply EXIF orientation, convert embedded ICC to sRGB."""
    if not path.is_file():
        raise SystemExit(f"image not found: {path}")
    img = Image.open(path)
    img.load()
    src_fmt = norm_format(img.format or path.suffix)
    orientation = img.getexif().get(0x0112, 1)
    oriented = ImageOps.exif_transpose(img)
    if orientation != 1 and job:
        job.op("orientation", exif=orientation)
    img = oriented if oriented is not None else img

    icc = img.info.get("icc_profile")
    if icc and img.mode in ("RGB", "RGBA"):
        try:
            src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            desc = ImageCms.getProfileDescription(src_profile).lower()
            if "srgb" not in desc:
                img = ImageCms.profileToProfile(
                    img, src_profile, ImageCms.createProfile("sRGB"), outputMode=img.mode
                )
                if job:
                    job.op("srgb", source_profile=desc.strip())
        except Exception as exc:  # noqa: BLE001
            if job:
                job.warn(f"icc conversion failed: {exc}")
    img.info.pop("icc_profile", None)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if has_alpha(img) else "RGB")
    return img, src_fmt


def has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def blur_score(img: Image.Image) -> float:
    """Variance of Laplacian on a <=1024px grayscale copy. Higher = sharper."""
    gray = ImageOps.contain(img.convert("L"), (1024, 1024))
    a = np.asarray(gray, dtype=np.float32)
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    return float(lap.var())


def image_info(path: Path, with_blur: bool = True) -> dict:
    with Image.open(path) as raw:
        raw.load()
        fmt = norm_format(raw.format or path.suffix)
        orientation = raw.getexif().get(0x0112, 1)
        icc = bool(raw.info.get("icc_profile"))
        alpha = has_alpha(raw)
        mode = raw.mode
        oriented = ImageOps.exif_transpose(raw) or raw
        width, height = oriented.size
        blur = blur_score(oriented) if with_blur else None
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "aspect": round(width / height, 4),
        "bytes": path.stat().st_size,
        "format": fmt,
        "mode": mode,
        "alpha": alpha,
        "exif_orientation": orientation,
        "icc": icc,
        "blur": None if blur is None else round(blur, 1),
    }


def summary(img: Image.Image, fmt: str, nbytes: int | None) -> dict:
    return {"width": img.width, "height": img.height, "format": fmt, "bytes": nbytes}


# --------------------------------------------------------------------------- geometry


def parse_aspect(text: str) -> float:
    if ":" in text:
        w, h = text.split(":", 1)
        return float(w) / float(h)
    if "x" in text.lower():
        w, h = parse_size(text)
        return w / h
    return float(text)


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().replace(":", "x").split("x", 1)
    return int(w), int(h)


def parse_color(text: str) -> tuple[int, int, int]:
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def crop_box(width: int, height: int, aspect: float, cx: float, cy: float) -> tuple[int, int, int, int]:
    """Largest box of `aspect` inside WxH, centred as close to (cx, cy) px as possible."""
    if width / height > aspect:
        new_h = height
        new_w = max(1, int(round(height * aspect)))
    else:
        new_w = width
        new_h = max(1, int(round(width / aspect)))
    x0 = int(round(min(max(cx - new_w / 2, 0), width - new_w)))
    y0 = int(round(min(max(cy - new_h / 2, 0), height - new_h)))
    return x0, y0, x0 + new_w, y0 + new_h


def subject_center(img: Image.Image, model_path: Path, job: Job) -> tuple[float, float] | None:
    """Centre of union of YOLO person boxes (fallback: any box). None if nothing found."""
    global _YOLO_MODEL
    try:
        from ultralytics import YOLO  # lazy: heavy import
    except ImportError:
        job.warn("ultralytics not installed; smart crop fell back to center")
        return None
    if not model_path.is_file():
        job.warn(f"yolo model not found: {model_path}; smart crop fell back to center")
        return None
    if _YOLO_MODEL is None:
        _YOLO_MODEL = YOLO(str(model_path))
    result = _YOLO_MODEL.predict(img.convert("RGB"), verbose=False, conf=0.25)[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        job.warn("smart crop: no detections, using center")
        return None
    xyxy = boxes.xyxy.cpu().numpy()
    cls = boxes.cls.cpu().numpy()
    persons = xyxy[cls == 0]
    chosen = persons if len(persons) else xyxy
    x0, y0 = chosen[:, 0].min(), chosen[:, 1].min()
    x1, y1 = chosen[:, 2].max(), chosen[:, 3].max()
    job.op("detect", subjects=int(len(chosen)), box=[int(x0), int(y0), int(x1), int(y1)])
    return float((x0 + x1) / 2), float((y0 + y1) / 2)


def crop_to_aspect(img: Image.Image, aspect: float, args: argparse.Namespace, job: Job) -> Image.Image:
    if abs(img.width / img.height - aspect) / aspect < ASPECT_TOL:
        return img
    center = None
    if getattr(args, "smart", False):
        center = subject_center(img, Path(args.yolo_model), job)
    if center is None:
        anchor = getattr(args, "anchor", "center")
        if anchor in ANCHORS:
            fx, fy = ANCHORS[anchor]
        else:
            fx, fy = (float(v) for v in anchor.split(","))
        center = (img.width * fx, img.height * fy)
    box = crop_box(img.width, img.height, aspect, *center)
    job.op("crop", box=list(box), aspect=round(aspect, 4))
    return img.crop(box)


def resize_to(img: Image.Image, width: int, height: int, fit: str, job: Job) -> Image.Image:
    if (width, height) == img.size:
        return img
    if fit == "cover":
        out = ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS)
    elif fit == "contain":
        out = ImageOps.contain(img, (width, height), Image.Resampling.LANCZOS)
    else:
        out = img.resize((width, height), Image.Resampling.LANCZOS)
    job.op("resize", to=[out.width, out.height], fit=fit)
    return out


def pad_to(img: Image.Image, width: int, height: int, fill: str, job: Job) -> Image.Image:
    if (width, height) == img.size:
        return img
    fg = ImageOps.contain(img, (width, height), Image.Resampling.LANCZOS)
    ox, oy = (width - fg.width) // 2, (height - fg.height) // 2
    if fill == "blur":
        bg = ImageOps.fit(img.convert("RGB"), (width, height), Image.Resampling.BILINEAR)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=max(width, height) / 40))
    elif fill == "edge":
        arr = np.asarray(fg.convert("RGB"))
        pad = ((oy, height - fg.height - oy), (ox, width - fg.width - ox), (0, 0))
        bg = Image.fromarray(np.pad(arr, pad, mode="edge"))
    else:
        bg = Image.new("RGB", (width, height), parse_color(fill))
    if has_alpha(fg):
        bg.paste(fg, (ox, oy), fg.getchannel("A"))
    else:
        bg.paste(fg.convert("RGB"), (ox, oy))
    job.op("pad", to=[width, height], fill=fill)
    return bg


def flatten(img: Image.Image, color: str, job: Job) -> Image.Image:
    if not has_alpha(img):
        return img.convert("RGB")
    bg = Image.new("RGB", img.size, parse_color(color))
    bg.paste(img, mask=img.getchannel("A"))
    job.op("flatten", color=color)
    return bg


# --------------------------------------------------------------------------- saving


def encode(img: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    kwargs: dict = {}
    if fmt == "jpg":
        kwargs = {"quality": quality, "optimize": True, "progressive": True, "subsampling": 0 if quality >= 90 else 2}
    elif fmt == "webp":
        kwargs = {"quality": quality, "method": 4}
    elif fmt == "png":
        kwargs = {"optimize": True, "compress_level": 9}
    img.save(buf, PIL_FORMAT[fmt], **kwargs)
    return buf.getvalue()


def save_under_limit(
    img: Image.Image, dest: Path, fmt: str, quality: int, max_bytes: int, job: Job, dry_run: bool
) -> tuple[Image.Image, int]:
    """Encode; if over max_bytes lower quality (95->60), then downscale by 0.9 steps."""
    q = quality
    cur = img
    while True:
        data = encode(cur, fmt, q)
        if not max_bytes or len(data) <= max_bytes:
            break
        if fmt in ("jpg", "webp") and q > 60:
            q -= 5
            continue
        nw, nh = int(cur.width * 0.9), int(cur.height * 0.9)
        if nw < 64 or nh < 64:
            job.warn(f"could not get under {max_bytes} bytes (got {len(data)})")
            break
        cur = cur.resize((nw, nh), Image.Resampling.LANCZOS)
        job.op("shrink_for_size", to=[nw, nh])
        q = quality if fmt == "png" else max(q, 80)
    job.op("encode", format=fmt, quality=q if fmt != "png" else None, bytes=len(data))
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return cur, len(data)


# --------------------------------------------------------------------------- io plumbing


def expand_inputs(paths: list[str], from_manifest: Path | None) -> list[Path]:
    out: list[Path] = []
    if from_manifest:
        data = json.loads(from_manifest.read_text(encoding="utf-8"))
        base = from_manifest.parent
        for entry in data:
            name = entry.get("file") or entry.get("dst")
            if name and entry.get("status") != "error":
                out.append(base / name if not Path(name).is_absolute() else Path(name))
    for text in paths:
        p = Path(text)
        if p.is_dir():
            out.extend(sorted(c for c in p.iterdir() if c.suffix.lower() in IMAGE_SUFFIXES))
        elif any(ch in text for ch in "*?["):
            out.extend(sorted(c for c in Path().glob(text) if c.suffix.lower() in IMAGE_SUFFIXES))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"input not found: {text}")
    if not out:
        raise SystemExit("no input images")
    seen: set[Path] = set()
    unique = []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(p)
    return unique


def choose_format(args: argparse.Namespace, src_fmt: str, preset: Preset | None) -> str:
    fmt = norm_format(getattr(args, "format", None))
    if fmt:
        return fmt
    if preset:
        return src_fmt if src_fmt in preset.formats else preset.default_format
    return src_fmt if src_fmt in PIL_FORMAT else "png"


def dest_path(src: Path, args: argparse.Namespace, fmt: str) -> Path:
    out_dir: Path = args.out
    dest = out_dir / f"{src.stem}{args.suffix}.{fmt}"
    if dest.resolve() == src.resolve() and not args.overwrite:
        raise SystemExit(f"refusing to overwrite source {src} (pass --overwrite or --out/--suffix)")
    return dest


def run_transform(args: argparse.Namespace, src: Path, transform, preset: Preset | None) -> dict:
    """Load -> transform(img, job, args) -> save. Returns manifest entry."""
    job = Job(src)
    img, src_fmt = load_image(src, job)
    before = summary(img, src_fmt, src.stat().st_size)
    fmt = choose_format(args, src_fmt, preset)
    dest = dest_path(src, args, fmt)
    if dest.exists() and not args.overwrite:
        return entry(job, dest, "skipped", before, None)

    img = transform(img, job, args)

    if fmt == "jpg" or (preset and not preset.allow_alpha):
        if has_alpha(img):
            img = flatten(img, args.flatten, job)
    max_bytes = args.max_bytes or (preset.max_bytes if preset else 0)
    img, nbytes = save_under_limit(img, dest, fmt, args.quality, max_bytes, job, args.dry_run)

    if preset:
        for problem in preset.violations(img.width, img.height, fmt, nbytes, has_alpha(img)):
            job.warn(f"{preset.name}: {problem}")
    return entry(job, dest, "dry-run" if args.dry_run else "ok", before, summary(img, fmt, nbytes))


def entry(job: Job, dest: Path | None, status: str, before: dict | None, after: dict | None, error: str | None = None) -> dict:
    return {
        "src": str(job.src),
        "dst": str(dest) if dest else None,
        "status": status,
        "before": before,
        "after": after,
        "ops": job.ops,
        "warnings": job.warnings,
        "error": error,
    }


def _worker(payload: tuple[str, dict, str]) -> dict:
    cmd_name, args_dict, path = payload
    args = argparse.Namespace(**args_dict)
    try:
        return TRANSFORMS[cmd_name](args, Path(path))
    except SystemExit as exc:
        return entry(Job(Path(path)), None, "error", None, None, str(exc))
    except Exception as exc:  # noqa: BLE001
        return entry(Job(Path(path)), None, "error", None, None, f"{type(exc).__name__}: {exc}")


def load_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def upsert(entries: list[dict], new: dict) -> None:
    for i, old in enumerate(entries):
        if old.get("src") == new["src"] and old.get("dst") == new["dst"]:
            if new["status"] != "skipped":
                entries[i] = new
            return
    entries.append(new)


def run_command(args: argparse.Namespace, cmd_name: str) -> int:
    paths = expand_inputs(args.paths, args.from_manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    args_dict = {k: v for k, v in vars(args).items() if k not in ("func", "paths")}
    payloads = [(cmd_name, args_dict, str(p)) for p in paths]

    workers = max(1, args.workers)
    if workers > 1 and len(payloads) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_worker, payloads))
    else:
        results = [_worker(p) for p in payloads]

    counts = {"ok": 0, "skipped": 0, "error": 0, "dry-run": 0}
    for res in results:
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        line = f"{res['status']:8} {res['src']}"
        if res.get("after"):
            a = res["after"]
            line += f" -> {Path(res['dst']).name} {a['width']}x{a['height']} {a['bytes'] / MB:.2f}MB"
        elif res.get("dst"):
            line += f" -> {Path(res['dst']).name}"
        if res.get("error"):
            line += f" :: {res['error']}"
        print(line, file=sys.stderr if res["status"] == "error" else sys.stdout)
        for w in res.get("warnings", []):
            print(f"         warn: {w}", file=sys.stderr)
        if not args.dry_run:
            upsert(manifest, res)
    if not args.dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"done {' '.join(f'{k}={v}' for k, v in counts.items() if v)} manifest={manifest_path}")
    return 1 if counts["error"] else 0


# --------------------------------------------------------------------------- transforms


def target_from_resize_args(img: Image.Image, args: argparse.Namespace, job: Job) -> tuple[int, int, str] | None:
    w, h = img.size
    preset = get_preset(args.preset, args.preset_file)
    fit = args.fit
    if preset and not (args.width or args.height or args.long_edge or args.short_edge or args.scale or args.area):
        size = preset.target_size(w, h)
        if size is None:
            if preset.max_side and max(w, h) > preset.max_side:
                scale = preset.max_side / max(w, h)
                return int(w * scale), int(h * scale), "contain"
            return None
        return size[0], size[1], "cover" if preset.sizes else "exact"
    if args.area:
        aw, ah = parse_size(args.area)
        tw, th = size_for_area(w, h, (aw, ah), args.snap or 1)
        return tw, th, "exact"
    if args.scale:
        return max(1, int(w * args.scale)), max(1, int(h * args.scale)), "exact"
    if args.long_edge:
        s = args.long_edge / max(w, h)
        return max(1, int(round(w * s))), max(1, int(round(h * s))), "exact"
    if args.short_edge:
        s = args.short_edge / min(w, h)
        return max(1, int(round(w * s))), max(1, int(round(h * s))), "exact"
    if args.width and args.height:
        return args.width, args.height, fit
    if args.width:
        return args.width, max(1, int(round(h * args.width / w))), "exact"
    if args.height:
        return max(1, int(round(w * args.height / h))), args.height, "exact"
    raise SystemExit("resize: give --preset, --width/--height, --long-edge, --short-edge, --scale or --area")


def t_resize(img: Image.Image, job: Job, args: argparse.Namespace) -> Image.Image:
    target = target_from_resize_args(img, args, job)
    if target is None:
        job.warn("no resize needed for preset")
        return img
    tw, th, fit = target
    if args.snap and args.snap > 1:
        tw, th = snap(tw, args.snap), snap(th, args.snap)
    if args.no_upscale and tw > img.width and th > img.height:
        job.warn(f"skipped upscale to {tw}x{th}")
        return img
    return resize_to(img, tw, th, fit, job)


def t_crop(img: Image.Image, job: Job, args: argparse.Namespace) -> Image.Image:
    if args.box:
        x, y, w, h = (int(v) for v in args.box.split(","))
        job.op("crop", box=[x, y, x + w, y + h])
        return img.crop((x, y, x + w, y + h))
    if args.size:
        tw, th = parse_size(args.size)
        img = crop_to_aspect(img, tw / th, args, job)
        return resize_to(img, tw, th, "exact", job)
    if args.aspect:
        return crop_to_aspect(img, parse_aspect(args.aspect), args, job)
    preset = get_preset(args.preset, args.preset_file)
    if preset:
        return crop_to_aspect(img, preset.target_aspect(img.width, img.height), args, job)
    raise SystemExit("crop: give --aspect, --size, --box or --preset")


def t_pad(img: Image.Image, job: Job, args: argparse.Namespace) -> Image.Image:
    if args.size:
        tw, th = parse_size(args.size)
    elif args.aspect:
        aspect = parse_aspect(args.aspect)
        if img.width / img.height > aspect:
            tw, th = img.width, int(round(img.width / aspect))
        else:
            tw, th = int(round(img.height * aspect)), img.height
    else:
        raise SystemExit("pad: give --aspect or --size")
    return pad_to(img, tw, th, args.fill, job)


def t_convert(img: Image.Image, job: Job, args: argparse.Namespace) -> Image.Image:
    if args.flatten_always:
        img = flatten(img, args.flatten, job)
    return img


def t_fit(img: Image.Image, job: Job, args: argparse.Namespace) -> Image.Image:
    preset = get_preset(args.preset, args.preset_file)
    if not preset:
        raise SystemExit("fit: --preset or --preset-file required")
    w, h = img.size
    target = preset.target_size(w, h)
    if target:
        tw, th = target
        if preset.area and args.no_upscale and w * h < tw * th:
            tw, th = size_for_area(w, h, (w, h), preset.multiple_of)
            job.warn(f"source smaller than {preset.area[0]}x{preset.area[1]} bucket; using {tw}x{th}")
        img = crop_to_aspect(img, tw / th, args, job)
        if (tw > img.width or th > img.height) and preset.sizes:
            job.warn(f"upscaling {img.width}x{img.height} -> {tw}x{th}")
        img = resize_to(img, tw, th, "cover", job)
    else:
        aspect = preset.target_aspect(w, h)
        img = crop_to_aspect(img, aspect, args, job)
        if preset.max_side and max(img.size) > preset.max_side:
            s = preset.max_side / max(img.size)
            img = resize_to(img, int(img.width * s), int(img.height * s), "contain", job)
        if preset.min_side and min(img.size) < preset.min_side:
            s = preset.min_side / min(img.size)
            job.warn(f"upscaling to meet min side {preset.min_side}")
            img = resize_to(img, int(round(img.width * s)), int(round(img.height * s)), "exact", job)
        if args.long_edge and max(img.size) > args.long_edge:
            s = args.long_edge / max(img.size)
            img = resize_to(img, int(round(img.width * s)), int(round(img.height * s)), "exact", job)
        if preset.multiple_of > 1:
            img = resize_to(img, snap(img.width, preset.multiple_of), snap(img.height, preset.multiple_of), "cover", job)
    return img


def make_transform_cmd(transform, needs_preset: bool = False):
    def run(args: argparse.Namespace, src: Path) -> dict:
        preset = get_preset(args.preset, args.preset_file) if hasattr(args, "preset") else None
        if needs_preset and not preset:
            raise SystemExit("--preset or --preset-file required")
        return run_transform(args, src, transform, preset)

    return run


TRANSFORMS = {
    "resize": make_transform_cmd(t_resize),
    "crop": make_transform_cmd(t_crop),
    "pad": make_transform_cmd(t_pad),
    "convert": make_transform_cmd(t_convert),
    "fit": make_transform_cmd(t_fit, needs_preset=True),
}


# --------------------------------------------------------------------------- non-transform commands


def cmd_presets(args: argparse.Namespace) -> int:
    for p in PRESETS.values():
        print(f"{p.name}")
        print(f"  {p.description}")
        if p.sizes:
            print("  sizes: " + ", ".join(f"{w}x{h}" for w, h in p.sizes))
        if p.area:
            print(f"  area: {p.area[0]}x{p.area[1]} (aspect follows input), multiple of {p.multiple_of}")
        rules = []
        if p.min_side:
            rules.append(f"min side {p.min_side}")
        if p.max_side:
            rules.append(f"max side {p.max_side}")
        if p.aspect_range != (0.0, 0.0):
            rules.append(f"aspect {p.aspect_range[0]}..{p.aspect_range[1]}")
        if p.max_bytes:
            rules.append(f"<= {p.max_bytes / MB:.1f}MB")
        if not p.allow_alpha:
            rules.append("no alpha")
        rules.append(f"formats {'/'.join(p.formats)} (default {p.default_format})")
        print("  " + "; ".join(rules))
        for n in p.notes:
            print(f"  - {n}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    paths = expand_inputs(args.paths, args.from_manifest)
    presets = [get_preset(args.preset, args.preset_file)] if (args.preset or args.preset_file) else list(PRESETS.values())
    rows = []
    for p in paths:
        info = image_info(p, with_blur=not args.no_blur)
        info["presets"] = {
            pr.name: pr.violations(info["width"], info["height"], info["format"], info["bytes"], info["alpha"])
            for pr in presets
        }
        rows.append(info)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for info in rows:
        flags = []
        if info["alpha"]:
            flags.append("alpha")
        if info["exif_orientation"] not in (1, None):
            flags.append(f"exif-orient={info['exif_orientation']}")
        if info["icc"]:
            flags.append("icc")
        blur = f" blur={info['blur']}" if info["blur"] is not None else ""
        print(
            f"{info['path']}: {info['width']}x{info['height']} ({info['aspect']}) {info['format']} "
            f"{info['bytes'] / MB:.2f}MB {info['mode']}{blur} {' '.join(flags)}"
        )
        for name, problems in info["presets"].items():
            status = "ok" if not problems else "; ".join(problems)
            print(f"    {name:12} {status}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    preset = get_preset(args.preset, args.preset_file)
    if not preset:
        raise SystemExit("check: --preset or --preset-file required")
    paths = expand_inputs(args.paths, args.from_manifest)
    failed = 0
    for p in paths:
        info = image_info(p, with_blur=False)
        problems = preset.violations(info["width"], info["height"], info["format"], info["bytes"], info["alpha"])
        if problems:
            failed += 1
            print(f"FAIL {p}: {'; '.join(problems)}")
            print(f"     fix: ./tools/prep fit --preset {preset.name} --smart {p}")
        else:
            print(f"ok   {p}")
    print(f"checked={len(paths)} failed={failed}")
    return 1 if failed else 0


def cmd_pair(args: argparse.Namespace) -> int:
    preset = get_preset(args.preset, args.preset_file)
    if not preset:
        raise SystemExit("pair: --preset or --preset-file required")
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    first_size = None
    for src in (Path(args.start), Path(args.end)):
        job = Job(src)
        img, src_fmt = load_image(src, job)
        before = summary(img, src_fmt, src.stat().st_size)
        fmt = norm_format(args.format) or preset.default_format
        dest = dest_path(src, args, fmt)
        img = t_fit(img, job, args)
        if first_size and img.size != first_size:
            img = resize_to(img, first_size[0], first_size[1], "cover", job)
        first_size = first_size or img.size
        if has_alpha(img) and (fmt == "jpg" or not preset.allow_alpha):
            img = flatten(img, args.flatten, job)
        img, nbytes = save_under_limit(img, dest, fmt, args.quality, args.max_bytes or preset.max_bytes, job, args.dry_run)
        for problem in preset.violations(img.width, img.height, fmt, nbytes, has_alpha(img)):
            job.warn(f"{preset.name}: {problem}")
        res = entry(job, dest, "dry-run" if args.dry_run else "ok", before, summary(img, fmt, nbytes))
        results.append(res)
        print(f"{res['status']:8} {src} -> {dest.name} {img.width}x{img.height} {nbytes / MB:.2f}MB")
        for w in job.warnings:
            print(f"         warn: {w}", file=sys.stderr)
    if not args.dry_run:
        manifest_path = args.out / MANIFEST_NAME
        manifest = load_manifest(manifest_path)
        for res in results:
            upsert(manifest, res)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_datauri(args: argparse.Namespace) -> int:
    src = Path(args.path)
    if not src.is_file():
        raise SystemExit(f"image not found: {src}")
    data = src.read_bytes()
    mime, _ = mimetypes.guess_type(src.name)
    mime = mime or "image/png"
    uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    preset = get_preset(args.preset, args.preset_file)
    print(f"binary={len(data) / MB:.2f}MB encoded={len(uri) / MB:.2f}MB mime={mime}", file=sys.stderr)
    if preset and preset.max_bytes and len(data) > preset.max_bytes:
        print(f"warn: exceeds {preset.name} limit {preset.max_bytes / MB:.1f}MB", file=sys.stderr)
    if preset and preset.name == "runway" and len(uri) > 5 * MB:
        print("warn: exceeds runway 5MB data-URI limit", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(uri, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(uri)
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    paths = expand_inputs(args.paths, args.from_manifest)
    thumb = args.thumb
    cols = max(1, min(args.cols, len(paths)))
    rows = (len(paths) + cols - 1) // cols
    label_h = 28
    cell_h = thumb + label_h
    sheet = Image.new("RGB", (cols * thumb, rows * cell_h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        img, _ = load_image(p)
        info = f"{img.width}x{img.height}"
        t = ImageOps.contain(img.convert("RGB"), (thumb, thumb))
        x, y = (i % cols) * thumb, (i // cols) * cell_h
        sheet.paste(t, (x + (thumb - t.width) // 2, y + (thumb - t.height) // 2))
        draw.text((x + 4, y + thumb + 2), p.name[:28], fill=(230, 230, 230))
        draw.text((x + 4, y + thumb + 14), info, fill=(160, 160, 160))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    print(f"wrote {out} ({len(paths)} images, {cols}x{rows})")
    return 0


def cmd_batch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    rest = list(args.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest or rest[0] not in TRANSFORMS:
        raise SystemExit(f"batch: first arg must be one of {', '.join(TRANSFORMS)}")
    inner = parser.parse_args(rest)
    if args.workers is not None:
        inner.workers = args.workers
    elif inner.workers == 1:
        inner.workers = max(1, (os.cpu_count() or 2) // 2)
    if args.from_manifest:
        inner.from_manifest = args.from_manifest
    return run_command(inner, rest[0])


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./tools/prep",
        description="Prepare images for image-to-video models.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    preset_p = argparse.ArgumentParser(add_help=False)
    preset_p.add_argument("--preset", choices=sorted(PRESETS), help="Model preset")
    preset_p.add_argument("--preset-file", type=Path, help="Custom preset JSON (overrides --preset)")

    inputs_p = argparse.ArgumentParser(add_help=False)
    inputs_p.add_argument("paths", nargs="*", help="Files, dirs or globs")
    inputs_p.add_argument("--from-manifest", type=Path, help="Take inputs from a manifest.json (generate.py or prep.py)")

    io_p = argparse.ArgumentParser(add_help=False, parents=[inputs_p])
    io_p.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output dir (default {DEFAULT_OUT})")
    io_p.add_argument("--suffix", default="", help="Appended to output stem")
    io_p.add_argument("--overwrite", action="store_true", help="Replace existing outputs")
    io_p.add_argument("--dry-run", action="store_true", help="Compute but do not write")
    io_p.add_argument("--workers", type=int, default=1, help="Parallel processes")
    io_p.add_argument("--format", choices=["jpg", "jpeg", "png", "webp"], help="Output format (default: keep / preset default)")
    io_p.add_argument("--quality", type=int, default=92, help="JPEG/WebP quality")
    io_p.add_argument("--max-bytes", type=int, default=0, help="Shrink until under this many bytes (default: preset limit)")
    io_p.add_argument("--flatten", default="#ffffff", help="Background color when alpha is removed")

    crop_p = argparse.ArgumentParser(add_help=False)
    crop_p.add_argument("--anchor", default="center", help="center|top|bottom|left|right or fx,fy (0..1)")
    crop_p.add_argument("--smart", action="store_true", help="Center crop on YOLO-detected subject")
    crop_p.add_argument("--yolo-model", type=Path, default=DEFAULT_YOLO)

    sub.add_parser("presets", help="List model presets")

    p = sub.add_parser("inspect", parents=[inputs_p, preset_p], help="Show image facts and per-preset compliance")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-blur", action="store_true", help="Skip blur score")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("check", parents=[inputs_p, preset_p], help="Validate against a preset (exit 1 on failure)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("resize", parents=[io_p, preset_p], help="Resize (Lanczos)")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--long-edge", type=int)
    p.add_argument("--short-edge", type=int)
    p.add_argument("--scale", type=float)
    p.add_argument("--area", help="WxH area budget, keep aspect")
    p.add_argument("--fit", choices=["contain", "cover", "exact"], default="contain", help="When both --width and --height given")
    p.add_argument("--snap", type=int, default=0, help="Round dims down to a multiple")
    p.add_argument("--no-upscale", action="store_true")

    p = sub.add_parser("crop", parents=[io_p, preset_p, crop_p], help="Crop to aspect / size / box")
    p.add_argument("--aspect", help="W:H")
    p.add_argument("--size", help="WxH (crop to aspect, then resize)")
    p.add_argument("--box", help="x,y,w,h in pixels")

    p = sub.add_parser("pad", parents=[io_p], help="Letterbox to aspect / size")
    p.add_argument("--aspect", help="W:H")
    p.add_argument("--size", help="WxH")
    p.add_argument("--fill", default="#000000", help="#rrggbb | blur | edge")

    p = sub.add_parser("convert", parents=[io_p], help="Change format / quality / size cap / alpha")
    p.add_argument("--flatten-always", action="store_true", help="Remove alpha even for png/webp")

    p = sub.add_parser("fit", parents=[io_p, preset_p, crop_p], help="One-shot: make image valid for a preset")
    p.add_argument("--no-upscale", action="store_true", help="Use a smaller area bucket instead of upscaling (area presets)")
    p.add_argument("--long-edge", type=int, help="Cap long edge (constraint-only presets like kling)")

    p = sub.add_parser("pair", parents=[preset_p, crop_p], help="Prepare start + end frame with identical dims")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--suffix", default="")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["jpg", "jpeg", "png", "webp"])
    p.add_argument("--quality", type=int, default=92)
    p.add_argument("--max-bytes", type=int, default=0)
    p.add_argument("--flatten", default="#ffffff")
    p.add_argument("--no-upscale", action="store_true")
    p.add_argument("--long-edge", type=int)
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("batch", help="Run a transform over many files in parallel: batch [opts] CMD [CMD opts]")
    p.add_argument("--workers", type=int, default=None, help="Default: half the CPUs")
    p.add_argument("--from-manifest", type=Path)
    p.add_argument("rest", nargs=argparse.REMAINDER)

    p = sub.add_parser("datauri", parents=[preset_p], help="Print base64 data URL, warn on provider limits")
    p.add_argument("path")
    p.add_argument("--out", help="Write URI to file instead of stdout")
    p.set_defaults(func=cmd_datauri)

    p = sub.add_parser("sheet", parents=[inputs_p], help="Contact sheet for QC")
    p.add_argument("--out", default="sheet.jpg")
    p.add_argument("--cols", type=int, default=5)
    p.add_argument("--thumb", type=int, default=256)
    p.set_defaults(func=cmd_sheet)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "presets":
        return cmd_presets(args)
    if args.cmd == "batch":
        return cmd_batch(args, parser)
    if args.cmd in TRANSFORMS:
        if not args.paths and not args.from_manifest:
            parser.error("no input paths")
        return run_command(args, args.cmd)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
