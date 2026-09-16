"""Run ffmpeg/ffprobe. Fail fast if binaries are missing."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path


def find_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"{name} not found on PATH. Install ffmpeg.")
    return path


def _print_argv(argv: list[str]) -> None:
    print(shlex.join(argv))


def run(
    argv: list[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str] | None:
    if dry_run:
        _print_argv(argv)
        return None
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        err = (result.stderr or "").strip() or f"exit {result.returncode}"
        raise SystemExit(f"{argv[0]} failed ({result.returncode}):\n{err}")
    return result


def ffmpeg(
    args: list[str],
    *,
    dry_run: bool = False,
    overwrite: bool = False,
    extra_global: list[str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    argv = [
        find_bin("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
    ]
    if extra_global:
        argv.extend(extra_global)
    argv.extend(args)
    return run(argv, dry_run=dry_run)


def ffprobe(
    args: list[str],
    *,
    dry_run: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | None:
    argv = [find_bin("ffprobe"), *args]
    return run(argv, dry_run=dry_run, check=check)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
