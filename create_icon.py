#!/usr/bin/env python3
# create_icon.py — generates platform-appropriate icon files from storm.png.
#
# Usage:
#   1. Save your icon as storm.png (1024x1024) in the project root.
#   2. Run:  python create_icon.py
#
# Output:
#   macOS   → storm.icns  (then re-run ./create_app.sh)
#   Windows → storm.ico   (then re-run create_app_windows.bat)
#   Both files are generated regardless of platform.
#
# Requires Pillow:  pip install pillow
# macOS also requires iconutil (built into macOS).

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC  = ROOT / "storm.png"

ICO_SIZES  = [16, 32, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def main():
    if not SRC.exists():
        print(f"Error: {SRC} not found.")
        print("Save a 1024×1024 PNG as storm.png in the project root and re-run.")
        sys.exit(1)

    try:
        from PIL import Image
    except ImportError:
        print("Pillow not found. Install with:  pip install pillow")
        sys.exit(1)

    img = Image.open(SRC).convert("RGBA")

    _make_ico(img)
    _make_icns(img)


def _make_ico(img):
    """Generate storm.ico for Windows."""
    from PIL import Image
    dest = ROOT / "storm.ico"
    sizes = [(s, s) for s in ICO_SIZES]
    frames = [img.resize(sz, Image.LANCZOS) for sz in sizes]
    frames[0].save(dest, format="ICO", sizes=sizes, append_images=frames[1:])
    print(f"Created {dest}")


def _make_icns(img):
    """Generate storm.icns for macOS (requires iconutil)."""
    if not shutil.which("iconutil"):
        print("Skipping storm.icns — iconutil not available (macOS only).")
        return

    from PIL import Image
    iconset = ROOT / "storm.iconset"
    iconset.mkdir(exist_ok=True)

    for size in ICNS_SIZES:
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(iconset / f"icon_{size}x{size}.png")
        if size <= 512:
            resized2 = img.resize((size * 2, size * 2), Image.LANCZOS)
            resized2.save(iconset / f"icon_{size}x{size}@2x.png")

    dest = ROOT / "storm.icns"
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(dest)],
        check=True,
    )
    shutil.rmtree(iconset)
    print(f"Created {dest}")


if __name__ == "__main__":
    main()
