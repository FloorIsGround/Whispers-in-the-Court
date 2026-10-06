"""Build one native executable with Python, Court Brain and the bundled mod.

Windows: py -3 tools/build_exe.py -> dist/WhispersInTheCourt.exe
Linux:   python3 tools/build_exe.py -> dist/WhispersInTheCourt

Windows retains its original icon/one-file/windowed build and automatic
PyInstaller installation; --no-install disables installation. Linux requires
an already prepared build environment and never installs packages. Both run
the local static mod validator before building. See packaging/linux/README.md
for source launching, asset policy and Linux libc compatibility limitations.
Player2 remains separate. No Wine is needed for the Linux companion.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "tools" / "court_brain"
MOD = ROOT / "mod" / "WhispersInTheCourt"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "WhispersInTheCourt"


# ----------------------------------------------------------------------
# The icon: drawn here (a gilt diamond on a navy seal), no game artwork
# ----------------------------------------------------------------------

def _png(size: int, pixels: bytearray) -> bytes:
    raw = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _draw(size: int) -> bytearray:
    px = bytearray(size * size * 4)
    c = (size - 1) / 2
    r_out, r_ring = size * 0.48, size * 0.43
    navy_hi, navy_lo = (0x2a, 0x44, 0x63), (0x0e, 0x17, 0x23)
    gold, gold_hi = (0xc9, 0xa4, 0x5c), (0xf0, 0xd9, 0x9c)
    ss = 3                                   # supersampling for smooth edges
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    fx, fy = x + (sx + 0.5) / ss - 0.5, y + (sy + 0.5) / ss - 0.5
                    dx, dy = fx - c, fy - c
                    d = math.hypot(dx, dy)
                    if d > r_out:
                        continue
                    t = min(1.0, max(0.0, (fy / size)))
                    col = tuple(navy_hi[i] + (navy_lo[i] - navy_hi[i]) * t for i in range(3))
                    if d > r_ring:                                  # the gilt rim
                        col = gold if d < r_out - size * 0.012 else tuple(v * 0.6 for v in gold)
                    else:
                        # a four-pointed diamond star, and a small one inside
                        star = abs(dx) / (size * 0.30) + abs(dy) / (size * 0.36)
                        cross = min(abs(dx), abs(dy)) < size * 0.018 and max(abs(dx), abs(dy)) < size * 0.40
                        if star <= 1.0:
                            shade = 0.75 + 0.25 * (1 - (dx + dy) / (size * 0.6))
                            col = tuple(min(255, gold[i] * shade) for i in range(3))
                            if star <= 0.42:
                                col = navy_lo if star > 0.30 else gold_hi
                        elif cross:
                            col = gold
                        elif abs(d - r_ring * 0.93) < size * 0.008:
                            col = tuple(v * 0.7 for v in gold)
                    for i in range(3):
                        acc[i] += col[i]
                    acc[3] += 255
            n = ss * ss
            p = (y * size + x) * 4
            if acc[3]:
                k = acc[3] / 255
                px[p:p + 4] = bytes((int(acc[0] / k), int(acc[1] / k), int(acc[2] / k), int(acc[3] / n)))
    return px


def make_icon(path: Path) -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [_png(s, _draw(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    path.write_bytes(head + entries + data)


# ----------------------------------------------------------------------

def executable_path(platform: str) -> Path:
    return DIST / (f"{NAME}.exe" if platform == "win32" else NAME)


def static_asset_manifest() -> list[tuple[Path, str]]:
    """Only reviewed upstream assets; never collect the source/config tree as data."""
    names = json.loads((ROOT / "packaging" / "linux" / "assets.json").read_text(encoding="utf-8"))
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError("Invalid static asset manifest")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate static asset manifest entry")
    manifest = []
    for name in names:
        relative = Path(name)
        parts = relative.parts
        private = any(
            part.lower() in {"config.json", "instructions.json", "logs", "log", "__pycache__"}
            or any(token in part.lower() for token in (".env", "api_key", "apikey", "secret", "credential"))
            or part.lower().endswith((".log", ".key", ".pem"))
            for part in parts
        )
        is_mod = parts[:2] == ("mod", NAME)
        is_icon = name == "tools/court_brain/courtbrain/icon.ico"
        if (relative.is_absolute() or ".." in parts or relative.as_posix() != name
                or private or not (is_mod or is_icon) or ":" in name or ";" in name):
            raise ValueError(f"Unsafe static asset manifest entry: {name}")
        source = ROOT / relative
        if any(p.is_symlink() for p in (source, *source.parents) if p != ROOT and ROOT in p.parents):
            raise ValueError(f"Symlink assets are not permitted: {name}")
        if not source.is_file():
            raise ValueError(f"Missing static asset: {name}")
        manifest.append((source, relative.parent.as_posix() if is_mod else "courtbrain"))
    return manifest


def build_arguments(platform: str, icon: Path) -> list[str]:
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile"]
    if platform == "win32":
        args += ["--windowed", "--name", NAME, "--icon", str(icon)]
    else:
        args += ["--name", NAME, "--collect-submodules", "courtbrain"]
    args += ["--paths", str(BRAIN)]
    if platform == "win32":
        # Keep the Windows command/output identical to the original builder.
        args += [
            "--add-data", f"{MOD};mod/WhispersInTheCourt",
            "--add-data", f"{BRAIN / 'courtbrain' / 'icon.ico'};courtbrain",
        ]
    else:
        for source, destination in static_asset_manifest():
            args += ["--add-data", f"{source}:{destination}"]
    args += [
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(BRAIN / f"{NAME}.py"),
    ]
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the native Whispers in the Court executable.")
    parser.add_argument("--no-install", action="store_true",
                        help="fail if PyInstaller is missing (always enabled on Linux)")
    options = parser.parse_args(argv)
    if sys.platform not in {"win32", "linux"}:
        print("Build on Windows or Linux for that same platform; cross-compiling is not supported.")
        return 1
    if sys.platform == "linux":
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print(f"PyInstaller is missing from {sys.executable}. Use a prepared build environment. "
                  "No packages were installed.")
            return 1
    try:
        manifest = static_asset_manifest()
        if sys.platform == "win32":
            # The unchanged Windows --add-data directory includes every file:
            # reject dirty/private additions rather than silently packaging them.
            reviewed = {source for source, _ in manifest if MOD in source.parents}
            actual = {p for p in MOD.rglob("*") if p.is_file() or p.is_symlink()}
            if actual != reviewed or MOD.is_symlink() or any(p.is_symlink() for p in MOD.rglob("*")):
                raise ValueError("Unreviewed files in the mod directory; use a clean source tree")
    except (OSError, ValueError) as exc:
        print(f"Cannot safely package assets: {exc}")
        return 1
    check = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_mod.py")])
    if check.returncode != 0:
        print("The mod has problems: fix them before building the exe.")
        return 1
    if sys.platform == "win32":
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            if options.no_install:
                print(f"PyInstaller is missing from {sys.executable}. No packages were installed.")
                return 1
            print("Installing PyInstaller (only needed to build the exe)…")
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)
    BUILD.mkdir(exist_ok=True)
    icon = BUILD / f"{NAME}.ico"
    if sys.platform == "win32":
        make_icon(icon)
        shutil.copyfile(icon, BRAIN / "courtbrain" / "icon.ico")      # also the windows' own icon
    subprocess.run(build_arguments(sys.platform, icon), check=True)
    exe = executable_path(sys.platform)
    print(f"\nReady: {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
