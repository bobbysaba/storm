"""Downloads and catalog listings for the FRDD/CLAMPS sources on NSSL THREDDS
(lidar winds, TROPoe, sondes, trailer surface met, CopterSonde, raw lidar).

Same rules as the one-second FOFS files (vehicle_obs_archive_fetcher.py):
STORM's NSSL SSL context, a browser-like User-Agent, retries on timeouts and
5xx only. Every file goes through core/package_sources, so a case package
can carry it. Raw lidar files (up to hundreds of MB) are streamed to a disk
cache instead of read into memory (`download_to_cache`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from archive.fetchers.vehicle_obs_archive_fetcher import _USER_AGENT, _urlopen_with_retry
from core import package_sources, provenance
from data import thredds_paths as paths

log = logging.getLogger(__name__)

_listings: dict[str, list[str]] = {}
_listings_lock = threading.Lock()


def read(kind: str, url: str, timeout: int = 60) -> bytes:
    """One file's bytes (a case package's copy first). HTTPError 404/410
    means there is no such file."""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    return package_sources.read_url(kind, request, _urlopen_with_retry, timeout=timeout)


def is_missing(exc: Exception) -> bool:
    """A real "nothing here" answer, as opposed to a failed request."""
    return isinstance(exc, HTTPError) and exc.code in (404, 410)


def list_folder(path: str) -> list[str]:
    """Names of the files directly in a THREDDS folder, from its catalog page.

    A missing folder (404 / 410) lists as empty; other failures raise. Lists
    are kept for the run: these folders hold years of files, and a past
    day's files don't change. An empty list isn't kept, in case it was a
    folder that isn't there yet."""
    path = path.strip("/")
    with _listings_lock:
        if path in _listings:
            return list(_listings[path])
    try:
        html = read("listings", paths.catalog_url(path), timeout=30).decode("utf-8", errors="replace")
    except Exception as exc:
        if is_missing(exc):
            return []
        raise
    names = re.findall(rf'dataset={re.escape(path)}/([^"/]+)"', html)
    names = list(dict.fromkeys(names))
    if names:
        with _listings_lock:
            _listings[path] = names
    return list(names)


def clear_listings() -> None:
    with _listings_lock:
        _listings.clear()


class Cancelled(Exception):
    """A download given up because its `cancel` event was set."""


def _check(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def download_to_cache(kind: str, url: str, cache_dir, filename: str, *, cancel=None,
                      max_bytes: int = 768 * 1024 * 1024, timeout: int = 60) -> Path:
    """A file kept on disk: <cache_dir>/<sha256 of url><suffix>, with a
    .json record of its hash. A cached copy is used when its hash still
    matches; else a case package's copy, else a streamed download (never
    left half-written). Each is recorded in the session's provenance."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + Path(filename).suffix)
    record = path.with_suffix(path.suffix + ".json")
    if path.exists() and record.exists():
        try:
            saved = json.loads(record.read_text())
            with path.open("rb") as cached:
                digest = hashlib.file_digest(cached, "sha256").hexdigest()
        except (OSError, ValueError):
            saved, digest = {}, None
        if digest is not None and saved.get("sha256") == digest:
            provenance.record(kind, url, sha256=digest, size=path.stat().st_size, source="cache")
            try:
                os.utime(path)            # recently used: prune_cache keeps it longest
            except OSError:
                pass
            return path
    _check(cancel)
    packaged = package_sources.get(url)
    if packaged is not None:
        path.write_bytes(packaged)
        _write_record(record, url, hashlib.sha256(packaged).hexdigest(), len(packaged))
        return path
    temporary = None
    try:
        digest, size = hashlib.sha256(), 0
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        with _urlopen_with_retry(request, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix=".part") as out:
                temporary = Path(out.name)
                while chunk := response.read(256 * 1024):
                    _check(cancel)
                    size += len(chunk)
                    if size > max_bytes:
                        raise OSError(f"{filename} is over {max_bytes // 2**20} MB; not downloaded")
                    digest.update(chunk)
                    out.write(chunk)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _write_record(record, url, digest.hexdigest(), size)
    provenance.record(kind, url, sha256=digest.hexdigest(), size=size)
    return path


def _write_record(record: Path, url: str, sha256: str, size: int) -> None:
    record.write_text(json.dumps({
        "url": url, "sha256": sha256, "bytes": size,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))


def is_cached(url: str, cache_dir, filename: str) -> bool:
    path = Path(cache_dir) / (hashlib.sha256(url.encode()).hexdigest() + Path(filename).suffix)
    return path.exists() and path.with_suffix(path.suffix + ".json").exists()


def prune_cache(cache_dir, max_bytes: int) -> int:
    """Delete the least recently used downloads (each with its record) until
    the folder fits in max_bytes. Returns the bytes freed."""
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return 0
    entries = []
    for record in cache_dir.glob("*.json"):
        data = record.with_suffix("")                   # "<sha>.cdf.json" -> "<sha>.cdf"
        try:
            if data.suffix and data.is_file():
                stat = data.stat()
                entries.append((stat.st_mtime, stat.st_size + record.stat().st_size, data, record))
        except OSError:
            continue
    total = sum(size for _, size, _, _ in entries)
    freed = 0
    for _, size, data, record in sorted(entries, key=lambda e: e[0]):
        if total - freed <= max_bytes:
            break
        try:
            data.unlink()
            record.unlink(missing_ok=True)
            freed += size
        except OSError:
            continue
    now = datetime.now(timezone.utc).timestamp()
    for stale in cache_dir.glob("*.part"):              # left by a crash mid-download
        try:
            if now - stale.stat().st_mtime > 3600:      # not another window's download
                stale.unlink(missing_ok=True)
        except OSError:
            continue
    return freed
