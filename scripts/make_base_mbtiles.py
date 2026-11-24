#!/usr/bin/env python3
"""
Build the offline vector base map, tiles/storm.mbtiles, for your own area.

STORM's map style reads the OpenMapTiles schema. This script runs Planetiler
(https://github.com/onthegomap/planetiler), which builds that schema from
OpenStreetMap: it downloads the area's extract from Geofabrik plus the
OpenStreetMap water polygons and Natural Earth (about 1.5 GB of shared files,
downloaded once), then writes the MBTiles file.

Needs Java 21 or newer. If you don't have it, envs/tiles.yml is a small
conda environment with it (kept apart from the storm environment):

    conda env create -f envs/tiles.yml
    conda activate tiles

Examples (run from the STORM folder; the area name is Geofabrik's):

    python scripts/make_base_mbtiles.py --area oklahoma
    python scripts/make_base_mbtiles.py --area germany
    python scripts/make_base_mbtiles.py --area us --bbox -105,30,-90,42

--bbox clips a large extract to the box you need, which is much faster and
smaller than the whole extract. A state builds in minutes; the whole US
takes hours, about 60 GB of disk while it runs and 16 GB or more of memory.
The download cache and work files go in ~/.cache/storm/planetiler.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PLANETILER_VERSION = "0.10.2"
# planetiler.jar.sha256 from the same release
PLANETILER_SHA256 = "f310bd0413e2e4512b27f4046d418664e8e1d3bf31603c2a70e23de06c167e4d"
PLANETILER_URL = (f"https://github.com/onthegomap/planetiler/releases/download/"
                  f"v{PLANETILER_VERSION}/planetiler.jar")
WORK_DIR = Path.home() / ".cache" / "storm" / "planetiler"
JAR = WORK_DIR / f"planetiler-{PLANETILER_VERSION}.jar"
OUTPUT = Path(__file__).resolve().parent.parent / "tiles" / "storm.mbtiles"
# Geofabrik's list of extracts. Planetiler 0.10.2 can't parse its current
# format, and its downloader doesn't follow Geofabrik's redirect from
# <name>-latest.osm.pbf to a dated file, so the extract is found and
# downloaded here and Planetiler gets the local file.
GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1-nogeom.json"
EXTRACT_MAX_AGE_S = 7 * 86400


def _java_major(java: str) -> int | None:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "(\d+)', out.stderr + out.stdout)
    return int(m.group(1)) if m else None


def _download_jar() -> None:
    if JAR.exists():
        return
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Planetiler {PLANETILER_VERSION} (about 90 MB) to {JAR}")
    part = JAR.with_suffix(".part")
    with urllib.request.urlopen(PLANETILER_URL, timeout=60) as resp, part.open("wb") as out:
        shutil.copyfileobj(resp, out)
    digest = hashlib.sha256(part.read_bytes()).hexdigest()
    if digest != PLANETILER_SHA256:
        part.unlink()
        raise SystemExit(f"Planetiler download doesn't match its checksum ({digest}); not using it.")
    part.replace(JAR)


def _extract_url(area: str) -> str | None:
    """The .osm.pbf URL of Geofabrik's extract *area* (an id such as
    rhode-island, or a path such as north-america/us/rhode-island)."""
    with urllib.request.urlopen(GEOFABRIK_INDEX, timeout=60) as resp:
        index = json.load(resp)
    area = area.strip("/")
    extracts = {f["properties"]["id"]: (f["properties"].get("urls") or {}).get("pbf")
                for f in index.get("features", []) if f.get("properties", {}).get("id")}
    if area in extracts:                         # germany, us/oklahoma
        return extracts[area]
    # oklahoma for us/oklahoma, north-america/us/oklahoma for us/oklahoma
    last = area.split("/")[-1]
    matches = [url for eid, url in extracts.items() if eid.split("/")[-1] == last]
    return matches[0] if len(matches) == 1 else None


def _download(url: str, dest: Path) -> None:
    part = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB", end="", flush=True)
    print()
    part.replace(dest)


def _fetch_extract(url: str, dest: Path) -> None:
    """Download the extract at *url* to *dest*, unless a copy under a week
    old is there. If <name>-latest fails (a new day's file isn't always
    served yet), try the newest dated copies in the same folder."""
    if dest.exists() and time.time() - dest.stat().st_mtime < EXTRACT_MAX_AGE_S:
        print(f"Using {dest} (under a week old)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    try:
        _download(url, dest)
        return
    except OSError as exc:
        print(f"  failed: {exc}")
    folder, latest = url.rsplit("/", 1)
    stem = latest.removesuffix("-latest.osm.pbf")
    with urllib.request.urlopen(folder + "/", timeout=60) as resp:
        listing = resp.read().decode("utf-8", errors="replace")
    dated = sorted(set(re.findall(rf'href="({re.escape(stem)}-\d{{6}}\.osm\.pbf)"', listing)), reverse=True)
    for name in dated[:3]:
        print(f"Trying {folder}/{name}")
        try:
            _download(f"{folder}/{name}", dest)
            return
        except OSError as exc:
            print(f"  failed: {exc}")
    raise SystemExit(f"Couldn't download the extract from {folder}/; try again later.")


def _args():
    parser = argparse.ArgumentParser(
        description="Build tiles/storm.mbtiles (OpenMapTiles schema) with Planetiler.")
    parser.add_argument("--area", required=True,
                        help="Geofabrik extract name, e.g. oklahoma, us, germany, australia")
    parser.add_argument("--bbox", metavar="W,S,E,N",
                        help="clip to this box in degrees (default: the whole extract)")
    parser.add_argument("--maxzoom", type=int, default=14, choices=range(6, 15), metavar="Z",
                        help="highest zoom, 6 to 14 (default: %(default)s)")
    parser.add_argument("--memory", default="4g",
                        help="Java heap, e.g. 4g or 16g (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=OUTPUT,
                        help="MBTiles file to write (default: tiles/storm.mbtiles)")
    parser.add_argument("--java", default=os.environ.get("JAVA", "java"),
                        help="java executable (default: java on PATH, or $JAVA)")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*", args.area):
        parser.error("--area is a Geofabrik name such as oklahoma or north-america/us")
    if args.bbox:
        try:
            west, south, east, north = (float(v) for v in args.bbox.split(","))
        except ValueError:
            parser.error("--bbox takes four numbers: west,south,east,north")
        if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
            parser.error("--bbox must be west,south,east,north with west < east and south < north")
    if not re.fullmatch(r"\d+[mMgG]", args.memory):
        parser.error("--memory is a size such as 4g")
    return args


def main() -> int:
    args = _args()
    major = _java_major(args.java)
    if major is None:
        print("Java not found. Install Java 21 or newer (see the top of this script).", file=sys.stderr)
        return 1
    if major < 21:
        print(f"Planetiler needs Java 21 or newer; {args.java} is Java {major}.", file=sys.stderr)
        return 1
    _download_jar()
    url = _extract_url(args.area)
    if not url:
        print(f"Geofabrik has no extract called {args.area!r}; see https://download.geofabrik.de "
              "for the names (the last part of an extract's address, e.g. rhode-island).", file=sys.stderr)
        return 1
    name = args.area.strip("/").split("/")[-1]
    extract = WORK_DIR / "data" / "sources" / f"{name}.osm.pbf"
    _fetch_extract(url, extract)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Planetiler writes to a new file; keep the old map until the new one is done
    building = output.with_name(output.stem + ".building.mbtiles")
    cmd = [args.java, f"-Xmx{args.memory}", "-jar", str(JAR),
           "--download", f"--osm_path={extract}",
           f"--maxzoom={args.maxzoom}",
           f"--output={building}", "--force"]
    if args.bbox:
        cmd.append(f"--bounds={args.bbox}")
    print("Running:", " ".join(cmd))
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    # Planetiler keeps its downloads and temp files under ./data
    result = subprocess.run(cmd, cwd=WORK_DIR)
    if result.returncode != 0:
        print(f"Planetiler failed (exit {result.returncode}); {output} is unchanged.", file=sys.stderr)
        return result.returncode
    building.replace(output)
    print(f"\nWrote {output} ({output.stat().st_size / 1024 ** 3:.2f} GB). Restart STORM to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
