"""Which source files this archive session loaded, for case packages
(core/case_package.py): URL, SHA-256 of the bytes received, size, and when.
The hash lets someone reopening a case tell whether the upstream file has
since changed: THREDDS collections are reorganized and reprocessed often.

Fetchers record through core/package_sources.py. MainWindow resets the
record when a window opens, and only an archive window turns it on, so live
modes (which share some fetchers) keep nothing. Thread-safe: fetchers run on
worker threads.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_active = False
_records: dict[str, dict] = {}
# Small files are kept as loaded, so a package holds exactly what was seen;
# larger ones (radar volumes, satellite scans, daily mesonet files) are
# downloaded again at export and checked against the recorded hash. The
# total is capped: hazard queries repeat on every clock step of a long session.
KEEP_BYTES_UP_TO = 2 * 1024 * 1024
KEEP_TOTAL_BYTES = 256 * 1024 * 1024
_kept: dict[str, bytes] = {}
_kept_total = 0


def reset(*, active: bool) -> None:
    """Forget everything; record from now on only if `active`."""
    global _active, _kept_total
    with _lock:
        _active = active
        _records.clear()
        _kept.clear()
        _kept_total = 0


def is_active() -> bool:
    with _lock:
        return _active


def record(kind: str, url: str, data: bytes | None = None, *, sha256: str | None = None,
           size: int | None = None, source: str = "network") -> None:
    """Note a loaded file. Pass the bytes, or a hash already computed.
    The first load of a URL is the one kept."""
    global _kept_total
    with _lock:
        if not _active or url in _records:
            return
    if data is not None:
        sha256 = hashlib.sha256(data).hexdigest()
        size = len(data)
    entry = {"kind": kind, "url": url, "sha256": sha256, "bytes": size, "source": source,
             "loaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with _lock:
        if not _active or url in _records:
            return
        _records[url] = entry
        if (data is not None and len(data) <= KEEP_BYTES_UP_TO
                and _kept_total + len(data) <= KEEP_TOTAL_BYTES):
            _kept[url] = bytes(data)
            _kept_total += len(data)


def kept_bytes(url: str) -> bytes | None:
    """The bytes as loaded, for files small enough to keep."""
    with _lock:
        return _kept.get(url)


def snapshot() -> list[dict]:
    with _lock:
        return sorted((dict(e) for e in _records.values()), key=lambda e: (e["kind"], e["url"]))
