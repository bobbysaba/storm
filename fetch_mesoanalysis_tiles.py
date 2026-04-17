#!/usr/bin/env python3
"""Pull STORM mesoanalysis vector-tile frames from a server cache.

This is the app/client-side companion to ../fetch_mesoanalysis.py. It expects a
server-published directory that can be served by plain HTTP/THREDDS fileServer:

  {BASE_URL}/latest.json
  {BASE_URL}/{variable}/latest.json
  {BASE_URL}/{variable}/{valid_time}/metadata.json
  {BASE_URL}/{variable}/{valid_time}/manifest.json
  {BASE_URL}/{variable}/{valid_time}/8/{x}/{y}.pbf

The script downloads the newest frame into a local cache using a staging
directory and atomic rename, so the app never sees partial frames.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import math
import os
import shutil
import ssl
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


# Fill this in once the THREDDS/fileServer URL is known. It should point to the
# directory containing latest.json, not to a specific variable.
DEFAULT_BASE_URL = ""
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "tiles" / "mesoanalysis"

DEFAULT_VARIABLES = [
    "temp",
    "srh01",
    "srh03",
    "sfclcl",
    "sfclfc",
    "sfccape",
    "sfccinh",
    "dwpt",
    "cape03",
]

ZOOM = 8
RETENTION_FRAMES = 4
REQUEST_TIMEOUT_SECONDS = 20
RETRIES = 3
MAX_WORKERS = 16
USER_AGENT = "STORM/mesoanalysis-tile-receiver"

# Same behavior as the existing current.json / NSSL THREDDS fetches. Some NSSL
# THREDDS cert chains have historically failed Python's default verification.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


@dataclass(frozen=True)
class Tile:
    z: int
    x: int
    y: int


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime


def clean_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise RuntimeError("Set --base-url or DEFAULT_BASE_URL")
    return url


def join_url(base: str, *parts: str) -> str:
    encoded = "/".join(quote(str(part).strip("/")) for part in parts if str(part))
    return f"{base.rstrip('/')}/{encoded}"


def fetch_bytes(url: str, allow_404: bool = False) -> bytes | None:
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CTX) as resp:
                return resp.read()
        except HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None
            if exc.code == 404:
                raise
            last_error = exc
        except URLError as exc:
            if allow_404 and isinstance(exc.reason, FileNotFoundError):
                return None
            last_error = exc

        if attempt < RETRIES:
            time.sleep(0.5 * attempt)

    assert last_error is not None
    raise last_error


def fetch_json(url: str) -> dict:
    data = fetch_bytes(url)
    if data is None:
        raise FileNotFoundError(url)
    return json.loads(data.decode("utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def lonlat_to_xyz(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_for_bbox(bbox: dict, zoom: int) -> list[str]:
    west = float(bbox["west"])
    south = float(bbox["south"])
    east = float(bbox["east"])
    north = float(bbox["north"])
    x_min, y_max = lonlat_to_xyz(west, south, zoom)
    x_max, y_min = lonlat_to_xyz(east, north, zoom)
    return [
        f"{zoom}/{x}/{y}.pbf"
        for x in range(min(x_min, x_max), max(x_min, x_max) + 1)
        for y in range(min(y_min, y_max), max(y_min, y_max) + 1)
    ]


def variables_from_args(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def pull_variable(
    base_url: str,
    variable: str,
    output_root: Path,
    expected_time: str | None,
    force: bool,
) -> str:
    var_latest = fetch_json(join_url(base_url, variable, "latest.json"))
    valid_time = str(var_latest.get("valid_time") or "")
    if not valid_time:
        raise RuntimeError(f"{variable}: latest.json has no valid_time")
    if expected_time and valid_time != expected_time:
        raise RuntimeError(
            f"{variable}: valid_time {valid_time} does not match root {expected_time}"
        )

    final_dir = output_root / variable / valid_time
    if final_dir.joinpath(".complete").exists() and not force:
        logging.info("%s %s already cached; skipping", variable, valid_time)
        return valid_time

    frame_url = join_url(base_url, variable, valid_time)
    manifest = fetch_json(join_url(frame_url, "manifest.json"))
    metadata = fetch_json(join_url(frame_url, "metadata.json"))

    tile_paths = manifest.get("tile_paths")
    if not isinstance(tile_paths, list):
        logging.warning("%s %s manifest has no tile_paths; falling back to bbox probes", variable, valid_time)
        tile_paths = tiles_for_bbox(manifest["bbox"], int(manifest.get("zoom", ZOOM)))

    tmp_parent = output_root / ".staging"
    tmp_parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{variable}_{valid_time}_", dir=str(tmp_parent)))

    try:
        write_json(staging_dir / "latest.json", var_latest)
        write_json(staging_dir / "manifest.json", manifest)
        write_json(staging_dir / "metadata.json", metadata)

        downloaded: list[str] = []
        missing: list[str] = []

        def download_tile(rel_path: str) -> tuple[str, bool]:
            data = fetch_bytes(join_url(frame_url, rel_path), allow_404=True)
            if data is None:
                return rel_path, False
            dest = staging_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return rel_path, True

        workers = min(MAX_WORKERS, max(1, len(tile_paths)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(download_tile, str(path)) for path in tile_paths]
            for future in as_completed(futures):
                rel_path, ok = future.result()
                if ok:
                    downloaded.append(rel_path)
                else:
                    missing.append(rel_path)

        downloaded.sort()
        missing.sort()
        local_manifest = dict(manifest)
        local_manifest.update(
            {
                "local_downloaded_tiles": len(downloaded),
                "local_missing_tiles": len(missing),
                "local_tile_paths": downloaded,
                "local_missing_tile_paths": missing,
                "source_base_url": base_url,
            }
        )
        write_json(staging_dir / "manifest.local.json", local_manifest)
        staging_dir.joinpath(".complete").write_text("complete\n", encoding="utf-8")

        if final_dir.exists():
            shutil.rmtree(final_dir)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_dir, final_dir)

        write_json(
            output_root / variable / "latest.json",
            {
                **var_latest,
                "local_path": str(final_dir),
                "local_tile_template": f"{valid_time}/{{z}}/{{x}}/{{y}}.pbf",
            },
        )
        logging.info(
            "%s %s cached: %d downloaded, %d missing",
            variable,
            valid_time,
            len(downloaded),
            len(missing),
        )
        return valid_time
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def prune_old_frames(output_root: Path, variable: str, retention: int) -> None:
    if retention < 1:
        return
    var_dir = output_root / variable
    if not var_dir.exists():
        return
    frames = sorted(p for p in var_dir.iterdir() if p.is_dir() and p.joinpath(".complete").exists())
    for old in frames[:-retention]:
        shutil.rmtree(old, ignore_errors=True)
        logging.info("%s pruned old frame %s", variable, old.name)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull latest STORM mesoanalysis tile frames from THREDDS/fileServer."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="server cache URL containing latest.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"local cache root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--variables",
        default=",".join(DEFAULT_VARIABLES),
        help="comma-separated variables to pull",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=RETENTION_FRAMES,
        help=f"completed local frames to retain per variable (default: {RETENTION_FRAMES})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="redownload even if the latest frame is already complete locally",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    base_url = clean_base_url(args.base_url)
    variables = variables_from_args(args.variables)
    if not variables:
        raise RuntimeError("No variables configured")

    root_latest = fetch_json(join_url(base_url, "latest.json"))
    expected_time = str(root_latest.get("valid_time") or "")
    if not expected_time:
        logging.warning("root latest.json has no valid_time; accepting per-variable times")
        expected_time = ""

    args.output_root.mkdir(parents=True, exist_ok=True)
    cached_times: dict[str, str] = {}
    for variable in variables:
        valid_time = pull_variable(
            base_url=base_url,
            variable=variable,
            output_root=args.output_root,
            expected_time=expected_time or None,
            force=args.force,
        )
        cached_times[variable] = valid_time
        prune_old_frames(args.output_root, variable, args.retention)

    write_json(
        args.output_root / "latest.json",
        {
            "source_base_url": base_url,
            "valid_time": expected_time or max(cached_times.values()),
            "variables": variables,
            "cached_times": cached_times,
            "retention": args.retention,
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        logging.exception("mesoanalysis tile pull failed: %s", exc)
        raise SystemExit(1)
