"""ffmpeg command builders for video-gen prep."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from vtools.probe import Probe, probe
from vtools.run import ffprobe

VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".ts",
    ".mts",
    ".m2ts",
    ".wmv",
}
IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}

SIZE_PRESETS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "wan480": (832, 480),
    "wan720": (1280, 720),
}
AR_PRESETS = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
}
CODEC_LIBS = {"h264": "libx264", "h265": "libx265", "hevc": "libx265"}
ENCODE_PRESETS = {
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
}


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def list_videos(path: Path) -> list[Path]:
    if path.is_file():
        if not is_video(path):
            raise SystemExit(f"not a video: {path}")
        return [path]
    if path.is_dir():
        videos = sorted(p for p in path.iterdir() if is_video(p))
        if not videos:
            raise SystemExit(f"no videos in {path}")
        return videos
    raise SystemExit(f"not found: {path}")


def list_images(path: Path) -> list[Path]:
    if path.is_file():
        if not is_image(path):
            raise SystemExit(f"not an image: {path}")
        return [path]
    if path.is_dir():
        images = sorted(p for p in path.iterdir() if is_image(p))
        if not images:
            raise SystemExit(f"no images in {path}")
        return images
    raise SystemExit(f"not found: {path}")


def parse_time(value: str) -> float:
    parts = value.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError as exc:
        raise SystemExit(f"invalid time: {value}") from exc
    raise SystemExit(f"invalid time: {value}")


def even(n: int) -> int:
    return n - (n % 2)


def parse_wxh(value: str) -> tuple[int, int]:
    lowered = value.lower()
    if "x" in lowered:
        left, right = lowered.split("x", 1)
    elif ":" in value:
        left, right = value.split(":", 1)
    else:
        raise SystemExit(f"invalid size: {value}")
    try:
        w, h = int(left), int(right)
    except ValueError as exc:
        raise SystemExit(f"invalid size: {value}") from exc
    if w <= 0 or h <= 0:
        raise SystemExit(f"invalid size: {value}")
    return w, h


def resolve_size(size: str, ar: str | None = None) -> tuple[int, int]:
    key = size.lower()
    if key in SIZE_PRESETS:
        w, h = SIZE_PRESETS[key]
    else:
        w, h = parse_wxh(size)
    if ar:
        if ar not in AR_PRESETS:
            raise SystemExit(f"unknown aspect ratio: {ar} (16:9, 9:16, 1:1, 4:3, 4:5)")
        ratio = AR_PRESETS[ar]
        current = w / h
        if abs(current - ratio) > 0.02:
            if (ratio < 1 <= current) or (ratio > 1 >= current):
                w, h = h, w
                current = w / h
        if abs(current - ratio) > 0.02:
            long_edge = max(w, h)
            if ratio >= 1:
                w, h = long_edge, even(max(2, round(long_edge / ratio)))
            else:
                h, w = long_edge, even(max(2, round(long_edge * ratio)))
    return even(w), even(h)


def _vf(*parts: str | None) -> list[str]:
    filters = [p for p in parts if p]
    if not filters:
        return []
    return ["-vf", ",".join(filters)]


def _encode_video(codec: str, crf: int, preset: str) -> list[str]:
    lib = CODEC_LIBS.get(codec, codec)
    if lib not in {"libx264", "libx265"}:
        raise SystemExit(f"unsupported codec: {codec} (h264, h265)")
    if preset not in ENCODE_PRESETS:
        raise SystemExit(f"unsupported preset: {preset}")
    args = ["-c:v", lib, "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]
    if lib == "libx265":
        args += ["-tag:v", "hvc1"]
    return args


def _mp4_flags() -> list[str]:
    return ["-movflags", "+faststart"]


def scale_filter(scale: str | None) -> str | None:
    if not scale:
        return None
    return f"scale={scale}"


def fit_filter(width: int, height: int, fit: str) -> str:
    if fit == "crop":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    if fit == "pad":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    raise SystemExit(f"unknown fit: {fit} (crop, pad)")


def compress_cmd(
    src: Path,
    dst: Path,
    *,
    crf: int = 23,
    scale: str | None = None,
    fps: float | None = None,
    codec: str = "h264",
    preset: str = "medium",
    no_audio: bool = False,
) -> list[str]:
    args = ["-i", str(src), *_vf(scale_filter(scale))]
    if fps:
        args += ["-r", str(fps)]
    args += _encode_video(codec, crf, preset)
    if no_audio:
        args += ["-an"]
    else:
        args += ["-c:a", "aac", "-b:a", "128k"]
    args += _mp4_flags()
    args.append(str(dst))
    return args


def extract_frames_cmds(
    src: Path,
    out_dir: Path,
    *,
    fps: float | None = None,
    every: int | None = None,
    first: bool = False,
    last: bool = False,
    fmt: str = "png",
    stem: str | None = None,
    info: Probe | None = None,
) -> list[list[str]]:
    if fps and every:
        raise SystemExit("use --fps or --every, not both")
    ext = ".jpg" if fmt in {"jpg", "jpeg"} else ".png"
    extra = ["-q:v", "2"] if ext == ".jpg" else []
    prefix = f"{stem}_" if stem else ""

    if first or last:
        cmds: list[list[str]] = []
        if first:
            dest = out_dir / f"{prefix}first{ext}"
            cmds.append(["-i", str(src), "-frames:v", "1", *extra, str(dest)])
        if last:
            dest = out_dir / f"{prefix}last{ext}"
            info = info or probe(src)
            if info.frames and info.frames > 0:
                n = info.frames - 1
                cmds.append(
                    [
                        "-i",
                        str(src),
                        "-vf",
                        f"select=eq(n\\,{n})",
                        "-frames:v",
                        "1",
                        *extra,
                        str(dest),
                    ]
                )
            else:
                cmds.append(
                    [
                        "-sseof",
                        "-0.1",
                        "-i",
                        str(src),
                        "-update",
                        "1",
                        *extra,
                        str(dest),
                    ]
                )
        return cmds

    pattern = f"{prefix}%06d{ext}"
    dest = str(out_dir / pattern)
    if fps:
        return [["-i", str(src), "-vf", f"fps={fps}", *extra, dest]]
    if every and every > 1:
        return [
            [
                "-i",
                str(src),
                "-vf",
                f"select=not(mod(n\\,{every}))",
                "-fps_mode",
                "vfr",
                *extra,
                dest,
            ]
        ]
    return [["-i", str(src), *extra, dest]]


def resize_cmd(
    src: Path,
    dst: Path,
    *,
    width: int,
    height: int,
    fit: str = "crop",
    crf: int = 18,
    codec: str = "h264",
    preset: str = "medium",
) -> list[str]:
    return [
        "-i",
        str(src),
        *_vf(fit_filter(width, height, fit)),
        *_encode_video(codec, crf, preset),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        *_mp4_flags(),
        str(dst),
    ]


def trim_cmd(
    src: Path,
    dst: Path,
    *,
    start: str | None = None,
    duration: str | None = None,
    end: str | None = None,
    frames: int | None = None,
    crf: int = 18,
    codec: str = "h264",
    preset: str = "medium",
) -> list[str]:
    start_s = parse_time(start) if start else 0.0
    dur_s: float | None = None
    if duration:
        dur_s = parse_time(duration)
    elif end:
        end_s = parse_time(end)
        dur_s = end_s - start_s
        if dur_s <= 0:
            raise SystemExit("--end must be after --start")

    if frames:
        args: list[str] = ["-i", str(src)]
        if start_s:
            args += ["-ss", str(start_s)]
        args += ["-frames:v", str(frames)]
        args += _encode_video(codec, crf, preset)
        args += ["-an"]
        args += _mp4_flags()
        args.append(str(dst))
        return args

    args = []
    if start_s:
        args += ["-ss", str(start_s)]
    args += ["-i", str(src)]
    if dur_s is not None:
        args += ["-t", str(dur_s)]
    args += ["-c", "copy", str(dst)]
    return args


def from_images_cmd(
    images: list[Path],
    dst: Path,
    list_path: Path,
    *,
    fps: float = 16,
    size: str | None = None,
    ar: str | None = None,
    crf: int = 18,
    codec: str = "h264",
    preset: str = "medium",
) -> tuple[str, list[str]]:
    if not images:
        raise SystemExit("no images to encode")
    duration = 1.0 / fps
    lines: list[str] = []
    for img in images:
        escaped = str(img.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"duration {duration}")
    last = str(images[-1].resolve()).replace("'", r"'\''")
    lines.append(f"file '{last}'")
    content = "\n".join(lines) + "\n"

    args = [
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-r",
        str(fps),
    ]
    if size:
        w, h = resolve_size(size, ar)
        args += _vf(fit_filter(w, h, "pad"))
    args += _encode_video(codec, crf, preset)
    args += ["-an", *_mp4_flags(), str(dst)]
    return content, args


def _lavfi_movie(path: Path) -> str:
    s = path.resolve().as_posix()
    return s.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def detect_scene_times(src: Path, threshold: float) -> list[float]:
    movie = _lavfi_movie(src)
    result = ffprobe(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            f"movie={movie},select=gt(scene\\,{threshold})",
            "-show_entries",
            "frame=pts_time,pkt_pts_time",
            "-of",
            "json",
        ]
    )
    assert result is not None
    data = json.loads(result.stdout or "{}")
    times: list[float] = []
    for frame in data.get("frames") or []:
        t = frame.get("pts_time") or frame.get("pkt_pts_time") or frame.get("best_effort_timestamp_time")
        if t in (None, "N/A"):
            continue
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    return sorted({round(t, 6) for t in times if t > 0})


def split_scene_cmds(
    src: Path,
    out_dir: Path,
    *,
    threshold: float = 0.3,
    stem: str | None = None,
    crf: int = 18,
    codec: str = "h264",
    preset: str = "medium",
    info: Probe | None = None,
) -> list[tuple[Path, list[str]]]:
    info = info or probe(src)
    duration = info.duration or 0.0
    cuts = detect_scene_times(src, threshold)
    starts = [0.0, *cuts]
    ends = [*cuts, duration]
    prefix = stem or src.stem
    jobs: list[tuple[Path, list[str]]] = []
    idx = 0
    for start, end in zip(starts, ends):
        length = end - start
        if length <= 0.05:
            continue
        dest = out_dir / f"{prefix}_{idx:03d}.mp4"
        args = [
            "-ss",
            str(start),
            "-i",
            str(src),
            "-t",
            str(length),
            *_encode_video(codec, crf, preset),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            *_mp4_flags(),
            str(dest),
        ]
        jobs.append((dest, args))
        idx += 1
    if not jobs:
        dest = out_dir / f"{prefix}_000.mp4"
        args = [
            "-i",
            str(src),
            *_encode_video(codec, crf, preset),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            *_mp4_flags(),
            str(dest),
        ]
        jobs.append((dest, args))
    return jobs


def audio_strip_cmd(src: Path, dst: Path) -> list[str]:
    return ["-i", str(src), "-c:v", "copy", "-an", str(dst)]


def audio_extract_cmd(src: Path, dst: Path, fmt: str) -> list[str]:
    args = ["-i", str(src), "-vn"]
    if fmt == "wav":
        args += ["-acodec", "pcm_s16le", str(dst)]
    elif fmt == "aac":
        args += ["-c:a", "aac", "-b:a", "192k", str(dst)]
    else:
        raise SystemExit(f"unknown audio format: {fmt} (wav, aac)")
    return args


def _copyable(probes: list[Probe]) -> bool:
    if not probes:
        return False
    first = probes[0]
    fps0 = round(first.fps or 0, 3)
    for p in probes:
        if (
            p.vcodec != first.vcodec
            or p.acodec != first.acodec
            or p.width != first.width
            or p.height != first.height
            or p.pix_fmt != first.pix_fmt
            or round(p.fps or 0, 3) != fps0
            or p.has_audio != first.has_audio
        ):
            return False
    return True


def concat_list_content(files: list[Path]) -> str:
    lines = []
    for f in files:
        escaped = str(f.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def concat_filter_cmd(files: list[Path], probes: list[Probe], dst: Path) -> list[str]:
    first = probes[0]
    w, h = first.width or 1280, first.height or 720
    w, h = even(w), even(h)
    fps = first.fps or 16
    n = len(files)
    has_audio = all(p.has_audio for p in probes)
    argv: list[str] = []
    for f in files:
        argv += ["-i", str(f)]
    filters: list[str] = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
    if has_audio:
        for i in range(n):
            filters.append(
                f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
            )
        pairs = "".join(f"[v{i}][a{i}]" for i in range(n))
        filters.append(f"{pairs}concat=n={n}:v=1:a=1[v][a]")
        argv += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            "[a]",
            *_encode_video("h264", 18, "medium"),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            *_mp4_flags(),
            str(dst),
        ]
    else:
        pairs = "".join(f"[v{i}]" for i in range(n))
        filters.append(f"{pairs}concat=n={n}:v=1:a=0[v]")
        argv += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-an",
            *_encode_video("h264", 18, "medium"),
            *_mp4_flags(),
            str(dst),
        ]
    return argv


def concat_plan(
    files: list[Path], dst: Path, list_path: Path
) -> tuple[bool, str, list[str]]:
    if len(files) < 2:
        raise SystemExit("concat needs at least two videos (or a folder with 2+)")
    probes = [probe(f) for f in files]
    if _copyable(probes):
        content = concat_list_content(files)
        args = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(dst)]
        return True, content, args
    return False, "", concat_filter_cmd(files, probes, dst)


def temp_concat_path() -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="vtools_concat_", suffix=".txt", delete=False)
    path = Path(handle.name)
    handle.close()
    return path
