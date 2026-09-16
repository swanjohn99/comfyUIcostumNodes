"""ffprobe JSON → Probe."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from vtools.run import ffprobe


@dataclass
class Probe:
    path: str
    width: int | None
    height: int | None
    fps: float | None
    duration: float | None
    frames: int | None
    vcodec: str | None
    acodec: str | None
    pix_fmt: str | None
    size_mb: float
    has_video: bool
    has_audio: bool

    def to_dict(self) -> dict:
        return asdict(self)

    def one_line(self) -> str:
        wh = f"{self.width}x{self.height}" if self.width and self.height else "no-video"
        fps = f"{self.fps:.2f}fps" if self.fps else "?fps"
        dur = f"{self.duration:.3f}s" if self.duration is not None else "?s"
        frames = f"{self.frames}f" if self.frames is not None else "?f"
        vcodec = self.vcodec or "-"
        acodec = self.acodec or "no-audio"
        return (
            f"{self.path}  {wh}  {fps}  {dur}  {frames}  {vcodec}  "
            f"{self.size_mb:.1f}MB  {acodec}"
        )


def parse_rate(rate: str | None) -> float | None:
    if not rate or rate in {"0/0", "N/A"}:
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            d = float(den)
        except ValueError:
            return None
        if d == 0:
            return None
        return float(num) / d
    try:
        return float(rate)
    except ValueError:
        return None


def _int(value: object) -> int | None:
    if value is None or value == "N/A":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def probe(path: Path) -> Probe:
    path = path.resolve()
    result = ffprobe(
        [
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    assert result is not None
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fps = None
    width = height = frames = None
    vcodec = pix_fmt = None
    duration = _float(fmt.get("duration"))
    if video:
        width = _int(video.get("width"))
        height = _int(video.get("height"))
        vcodec = video.get("codec_name")
        pix_fmt = video.get("pix_fmt")
        fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
        frames = _int(video.get("nb_frames"))
        if duration is None:
            duration = _float(video.get("duration"))
        if frames is None and duration is not None and fps:
            frames = int(round(duration * fps))

    size_bytes = _float(fmt.get("size")) or 0.0
    try:
        size_bytes = float(path.stat().st_size)
    except OSError:
        pass

    return Probe(
        path=str(path),
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        frames=frames,
        vcodec=vcodec,
        acodec=audio.get("codec_name") if audio else None,
        pix_fmt=pix_fmt,
        size_mb=size_bytes / (1024 * 1024),
        has_video=video is not None,
        has_audio=audio is not None,
    )
