#!/usr/bin/env python3
"""Dispatch commit-and-push to .ps1 on Windows, .sh elsewhere. Fail open."""
from __future__ import annotations

import os
import subprocess
import sys


def _drain_stdin() -> None:
    try:
        sys.stdin.read()
    except Exception:
        pass


def _powershell() -> list[str]:
    # Prefer Windows PowerShell; fall back to pwsh if present.
    for exe in ("powershell", "pwsh"):
        try:
            subprocess.run(
                [exe, "-NoProfile", "-Command", "exit 0"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return [exe]
        except FileNotFoundError:
            continue
    return ["powershell"]


def main() -> int:
    _drain_stdin()
    here = os.path.dirname(os.path.abspath(__file__))
    win = os.name == "nt" or sys.platform.startswith("win")

    if win:
        script = os.path.join(here, "commit-and-push.ps1")
        cmd = _powershell() + [
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
        ]
    else:
        script = os.path.join(here, "commit-and-push.sh")
        cmd = ["bash", script]

    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, check=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
