#!/usr/bin/env python3
"""ffmpeg toolkit for video-gen prep."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from vtools.ops import (
    VIDEO_SUFFIXES,
    audio_extract_cmd,
    audio_strip_cmd,
    compress_cmd,
    concat_plan,
    extract_frames_cmds,
    from_images_cmd,
    list_images,
    list_videos,
    resize_cmd,
    resolve_size,
    split_scene_cmds,
    temp_concat_path,
    trim_cmd,
)
from vtools.probe import probe
from vtools.run import ensure_parent, ffmpeg, find_bin


def parse_args() -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-y", "--overwrite", action="store_true", help="Overwrite existing outputs")
    common.add_argument("--dry-run", action="store_true", help="Print ffmpeg argv and exit")
    common.add_argument("--jobs", type=int, default=1, help="Parallel ffmpeg processes for folder inputs")

    parser = argparse.ArgumentParser(
        prog="./tools/video",
        description="ffmpeg helpers for video-gen: compress, frames, resize, trim, concat.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", parents=[common], help="Print res/fps/duration/codec/size")
    p_probe.add_argument("inputs", nargs="+", type=Path)
    p_probe.add_argument("--json", action="store_true", help="JSON output")

    p_compress = sub.add_parser("compress", parents=[common], help="CRF size-reduce for upload")
    p_compress.add_argument("input", type=Path)
    p_compress.add_argument("-o", "--out", type=Path, required=True)
    p_compress.add_argument("--crf", type=int, default=23)
    p_compress.add_argument("--scale", default="", help="e.g. 1280:-2 or 1280:720")
    p_compress.add_argument("--fps", type=float, default=0)
    p_compress.add_argument("--codec", default="h264", choices=["h264", "h265"])
    p_compress.add_argument("--preset", default="medium")
    p_compress.add_argument("--no-audio", action="store_true")

    p_frames = sub.add_parser("extract-frames", parents=[common], help="Extract PNG/JPEG frames")
    p_frames.add_argument("input", type=Path)
    p_frames.add_argument("-o", "--out", type=Path, required=True, help="Output directory")
    p_frames.add_argument("--fps", type=float, default=0)
    p_frames.add_argument("--every", type=int, default=0, help="Keep every Nth frame")
    p_frames.add_argument("--first", action="store_true", help="I2V start frame")
    p_frames.add_argument("--last", action="store_true", help="I2V end frame")
    p_frames.add_argument("--format", dest="fmt", default="png", choices=["png", "jpg", "jpeg"])

    p_resize = sub.add_parser("resize", parents=[common], help="Resize/crop to model resolution")
    p_resize.add_argument("input", type=Path)
    p_resize.add_argument("-o", "--out", type=Path, required=True)
    p_resize.add_argument(
        "--size",
        required=True,
        help="720p, 480p, 1080p, wan480, wan720, or WxH (1280x720)",
    )
    p_resize.add_argument("--ar", default="", help="16:9, 9:16, 1:1, 4:3, 4:5")
    p_resize.add_argument("--fit", default="crop", choices=["crop", "pad"])
    p_resize.add_argument("--crf", type=int, default=18)
    p_resize.add_argument("--codec", default="h264", choices=["h264", "h265"])
    p_resize.add_argument("--preset", default="medium")

    p_trim = sub.add_parser("trim", parents=[common], help="Cut by time or frame count")
    p_trim.add_argument("input", type=Path)
    p_trim.add_argument("-o", "--out", type=Path, required=True)
    p_trim.add_argument("--start", default="")
    p_trim.add_argument("--duration", default="")
    p_trim.add_argument("--end", default="")
    p_trim.add_argument("--frames", type=int, default=0, help="Frame count from --start (e.g. 81 for Wan)")
    p_trim.add_argument("--crf", type=int, default=18)
    p_trim.add_argument("--codec", default="h264", choices=["h264", "h265"])
    p_trim.add_argument("--preset", default="medium")

    p_imgs = sub.add_parser("from-images", parents=[common], help="Build mp4 from an image folder")
    p_imgs.add_argument("inputs", nargs="+", type=Path)
    p_imgs.add_argument("-o", "--out", type=Path, required=True)
    p_imgs.add_argument("--fps", type=float, default=16)
    p_imgs.add_argument("--size", default="")
    p_imgs.add_argument("--ar", default="")
    p_imgs.add_argument("--crf", type=int, default=18)
    p_imgs.add_argument("--codec", default="h264", choices=["h264", "h265"])
    p_imgs.add_argument("--preset", default="medium")

    p_scenes = sub.add_parser("split-scenes", parents=[common], help="Split on scene changes")
    p_scenes.add_argument("input", type=Path)
    p_scenes.add_argument("-o", "--out", type=Path, required=True, help="Output directory")
    p_scenes.add_argument("--threshold", type=float, default=0.3)
    p_scenes.add_argument("--crf", type=int, default=18)
    p_scenes.add_argument("--codec", default="h264", choices=["h264", "h265"])
    p_scenes.add_argument("--preset", default="medium")

    p_audio = sub.add_parser("audio", parents=[common], help="Strip or extract audio")
    p_audio.add_argument("input", type=Path)
    p_audio.add_argument("-o", "--out", type=Path, required=True)
    mode = p_audio.add_mutually_exclusive_group(required=True)
    mode.add_argument("--strip", action="store_true", help="Mute video (copy video stream)")
    mode.add_argument("--extract", action="store_true", help="Write audio-only file")
    p_audio.add_argument("--format", dest="fmt", default="wav", choices=["wav", "aac"])

    p_concat = sub.add_parser("concat", parents=[common], help="Join clips")
    p_concat.add_argument("inputs", nargs="+", type=Path)
    p_concat.add_argument("-o", "--out", type=Path, required=True)

    return parser.parse_args()


def skip_exists(path: Path, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        print(f"skip exists: {path}")
        return True
    return False


def check_not_same(src: Path, dst: Path) -> None:
    try:
        if src.resolve() == dst.resolve():
            raise SystemExit(f"in-place not supported: {src}")
    except FileNotFoundError:
        return


def out_is_dir(out: Path, batch: bool, force_dir: bool = False) -> bool:
    if force_dir:
        return True
    if out.exists():
        return out.is_dir()
    if batch:
        return True
    return out.suffix == ""


def dest_file(src: Path, out: Path, batch: bool, suffix: str, force_dir: bool = False) -> Path:
    if out_is_dir(out, batch, force_dir=force_dir):
        if batch and out.suffix.lower() in VIDEO_SUFFIXES:
            raise SystemExit("directory input requires directory -o")
        return out / f"{src.stem}{suffix}"
    return out


def run_pool(items: list, jobs: int, dry_run: bool, fn) -> None:
    if not items:
        return
    if dry_run or jobs <= 1 or len(items) == 1:
        for item in items:
            fn(item)
        return
    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = [pool.submit(fn, item) for item in items]
        for fut in as_completed(futs):
            exc = fut.exception()
            if exc:
                errors.append(exc)
    if errors:
        raise SystemExit(str(errors[0]))


def go(args_list: list[str], dry_run: bool, overwrite: bool) -> None:
    ffmpeg(args_list, dry_run=dry_run, overwrite=overwrite)


def cmd_probe(args: argparse.Namespace) -> None:
    files: list[Path] = []
    for item in args.inputs:
        files.extend(list_videos(item))
    rows = [probe(path) for path in files]
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], indent=2))
        return
    for row in rows:
        print(row.one_line())


def cmd_compress(args: argparse.Namespace) -> None:
    videos = list_videos(args.input)
    batch = args.input.is_dir() or len(videos) > 1

    def one(src: Path) -> None:
        dst = dest_file(src, args.out, batch, ".mp4")
        check_not_same(src, dst)
        if skip_exists(dst, args.overwrite):
            return
        if not args.dry_run:
            ensure_parent(dst)
        cmd = compress_cmd(
            src,
            dst,
            crf=args.crf,
            scale=args.scale or None,
            fps=args.fps or None,
            codec=args.codec,
            preset=args.preset,
            no_audio=args.no_audio,
        )
        print(f"compress {src.name} -> {dst}")
        go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_extract_frames(args: argparse.Namespace) -> None:
    videos = list_videos(args.input)
    batch = args.input.is_dir() or len(videos) > 1
    out_dir = args.out
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    def one(src: Path) -> None:
        stem = src.stem if batch else None
        cmds = extract_frames_cmds(
            src,
            out_dir,
            fps=args.fps or None,
            every=args.every or None,
            first=args.first,
            last=args.last,
            fmt=args.fmt,
            stem=stem,
        )
        print(f"extract-frames {src.name} -> {out_dir}")
        for cmd in cmds:
            dst = Path(cmd[-1])
            if "%" not in dst.name and skip_exists(dst, args.overwrite):
                continue
            if not args.dry_run:
                ensure_parent(dst)
            go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_resize(args: argparse.Namespace) -> None:
    videos = list_videos(args.input)
    batch = args.input.is_dir() or len(videos) > 1
    width, height = resolve_size(args.size, args.ar or None)

    def one(src: Path) -> None:
        dst = dest_file(src, args.out, batch, ".mp4")
        check_not_same(src, dst)
        if skip_exists(dst, args.overwrite):
            return
        if not args.dry_run:
            ensure_parent(dst)
        cmd = resize_cmd(
            src,
            dst,
            width=width,
            height=height,
            fit=args.fit,
            crf=args.crf,
            codec=args.codec,
            preset=args.preset,
        )
        print(f"resize {src.name} -> {dst} {width}x{height} {args.fit}")
        go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_trim(args: argparse.Namespace) -> None:
    if not (args.start or args.duration or args.end or args.frames):
        raise SystemExit("trim needs --start, --duration, --end, and/or --frames")
    videos = list_videos(args.input)
    batch = args.input.is_dir() or len(videos) > 1

    def one(src: Path) -> None:
        dst = dest_file(src, args.out, batch, ".mp4")
        check_not_same(src, dst)
        if skip_exists(dst, args.overwrite):
            return
        if not args.dry_run:
            ensure_parent(dst)
        cmd = trim_cmd(
            src,
            dst,
            start=args.start or None,
            duration=args.duration or None,
            end=args.end or None,
            frames=args.frames or None,
            crf=args.crf,
            codec=args.codec,
            preset=args.preset,
        )
        print(f"trim {src.name} -> {dst}")
        go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_from_images(args: argparse.Namespace) -> None:
    images: list[Path] = []
    if len(args.inputs) == 1 and args.inputs[0].is_dir():
        images = list_images(args.inputs[0])
    else:
        for item in args.inputs:
            if item.is_dir():
                images.extend(list_images(item))
            elif item.is_file():
                images.append(item)
            else:
                raise SystemExit(f"not found: {item}")
        images = sorted(images)
    if len(images) < 2:
        raise SystemExit("from-images needs at least two images")
    dst = args.out
    check_not_same(images[0], dst)
    if skip_exists(dst, args.overwrite):
        return
    list_path = temp_concat_path()
    content, cmd = from_images_cmd(
        images,
        dst,
        list_path,
        fps=args.fps,
        size=args.size or None,
        ar=args.ar or None,
        crf=args.crf,
        codec=args.codec,
        preset=args.preset,
    )
    print(f"from-images {len(images)} images -> {dst}")
    if args.dry_run:
        print("--- concat list ---")
        print(content, end="")
        cmd = [list_path.as_posix() if part == str(list_path) else part for part in cmd]
        go(cmd, True, args.overwrite)
        try:
            os.unlink(list_path)
        except OSError:
            pass
        return
    list_path.write_text(content, encoding="utf-8")
    ensure_parent(dst)
    try:
        go(cmd, False, args.overwrite)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


def cmd_split_scenes(args: argparse.Namespace) -> None:
    videos = list_videos(args.input)
    out_dir = args.out
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    def one(src: Path) -> None:
        jobs = split_scene_cmds(
            src,
            out_dir,
            threshold=args.threshold,
            stem=src.stem,
            crf=args.crf,
            codec=args.codec,
            preset=args.preset,
        )
        print(f"split-scenes {src.name} -> {len(jobs)} clips")
        for dest, cmd in jobs:
            if skip_exists(dest, args.overwrite):
                continue
            if not args.dry_run:
                ensure_parent(dest)
            go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_audio(args: argparse.Namespace) -> None:
    videos = list_videos(args.input)
    batch = args.input.is_dir() or len(videos) > 1
    if args.extract:
        suffix = ".wav" if args.fmt == "wav" else ".m4a"
    else:
        suffix = ".mp4"

    def one(src: Path) -> None:
        dst = dest_file(src, args.out, batch, suffix)
        check_not_same(src, dst)
        if skip_exists(dst, args.overwrite):
            return
        if not args.dry_run:
            ensure_parent(dst)
        if args.strip:
            cmd = audio_strip_cmd(src, dst)
            print(f"audio-strip {src.name} -> {dst}")
        else:
            info = probe(src)
            if not info.has_audio:
                raise SystemExit(f"no audio stream: {src}")
            cmd = audio_extract_cmd(src, dst, args.fmt)
            print(f"audio-extract {src.name} -> {dst}")
        go(cmd, args.dry_run, args.overwrite)

    run_pool(videos, args.jobs, args.dry_run, one)


def cmd_concat(args: argparse.Namespace) -> None:
    files: list[Path] = []
    for item in args.inputs:
        if item.is_dir():
            files.extend(list_videos(item))
        else:
            files.extend(list_videos(item))
    files = sorted(files) if any(p.is_dir() for p in args.inputs) and len(args.inputs) == 1 else files
    dst = args.out
    if skip_exists(dst, args.overwrite):
        return
    list_path = temp_concat_path()
    copyable, content, cmd = concat_plan(files, dst, list_path)
    print(f"concat {len(files)} clips -> {dst} ({'copy' if copyable else 're-encode'})")
    if copyable:
        if args.dry_run:
            print("--- concat list ---")
            print(content, end="")
            go(cmd, True, args.overwrite)
            try:
                os.unlink(list_path)
            except OSError:
                pass
            return
        list_path.write_text(content, encoding="utf-8")
        ensure_parent(dst)
        try:
            go(cmd, False, args.overwrite)
        finally:
            try:
                os.unlink(list_path)
            except OSError:
                pass
        return
    try:
        os.unlink(list_path)
    except OSError:
        pass
    if not args.dry_run:
        ensure_parent(dst)
    go(cmd, args.dry_run, args.overwrite)


HANDLERS = {
    "probe": cmd_probe,
    "compress": cmd_compress,
    "extract-frames": cmd_extract_frames,
    "resize": cmd_resize,
    "trim": cmd_trim,
    "from-images": cmd_from_images,
    "split-scenes": cmd_split_scenes,
    "audio": cmd_audio,
    "concat": cmd_concat,
}


def main() -> None:
    find_bin("ffmpeg")
    find_bin("ffprobe")
    args = parse_args()
    HANDLERS[args.cmd](args)


if __name__ == "__main__":
    main()
