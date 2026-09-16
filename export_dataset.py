#!/usr/bin/env python3
"""Export generated images + LoRA captions into a training dataset folder."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export output images and captions for LoRA training.")
    parser.add_argument("--manifest", default="output/manifest.json", type=Path)
    parser.add_argument("--images", default="output", type=Path)
    parser.add_argument("--out", default="dataset", type=Path)
    parser.add_argument("--trigger", default="", help="Override trigger token from manifest entries")
    return parser.parse_args()


def load_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"invalid manifest format: {path}")
    return data


def format_caption(entry: dict, trigger_override: str) -> str:
    caption = (entry.get("caption") or "").strip()
    trigger = (trigger_override or entry.get("trigger") or "").strip()
    if not caption:
        raise ValueError(f"missing caption for index {entry.get('index')}")
    if trigger:
        return f"{trigger}, {caption}"
    return caption


def export_dataset(manifest: list[dict], images_dir: Path, out_dir: Path, trigger: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = 0
    for entry in sorted(manifest, key=lambda item: item.get("index", 0)):
        if entry.get("status") == "error":
            continue
        filename = entry.get("file")
        if not filename:
            continue
        src = images_dir / filename
        if not src.is_file():
            print(f"skip missing image: {src}")
            continue
        stem = src.stem
        dest_img = out_dir / f"{stem}{src.suffix.lower()}"
        dest_txt = out_dir / f"{stem}.txt"
        caption = format_caption(entry, trigger)
        shutil.copy2(src, dest_img)
        dest_txt.write_text(caption + "\n", encoding="utf-8")
        print(f"exported {dest_img.name} + {dest_txt.name}")
        exported += 1
    return exported


def main() -> None:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    count = export_dataset(manifest, args.images, args.out, args.trigger)
    print(f"done exported={count} dataset={args.out}")


if __name__ == "__main__":
    main()
