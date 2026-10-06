#!/usr/bin/env python3
"""Launch unpacked Court Brain with this Python; no installation or cwd assumptions."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = ROOT / "tools" / "court_brain" / "WhispersInTheCourt.py"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 10):
        print("Court Brain requires Python 3.10 or newer.", file=sys.stderr)
        return 1
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(f"{sys.executable} has no working tkinter. Choose a Python with Tcl/Tk support "
              "(on Debian/Ubuntu, the system Python normally needs python3-tk). "
              "This launcher does not install packages.", file=sys.stderr)
        return 1
    if not ENTRY_POINT.is_file():
        print(f"Missing source entry point: {ENTRY_POINT}. Keep this launcher in the unpacked repository.",
              file=sys.stderr)
        return 1
    if args == ["--launcher-check"]:
        print(f"Python/Tk available; source entry point: {ENTRY_POINT}")
        return 0
    os.execv(sys.executable, [sys.executable, str(ENTRY_POINT), *args])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
