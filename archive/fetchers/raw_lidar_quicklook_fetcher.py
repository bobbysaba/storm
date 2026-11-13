"""Background loading of the raw lidar files for an archive session
(raw_lidar_archive_fetcher.py does the reading): find every lidar's PPI and
CSM files for the session's two UTC days, read each trailer's site from a
file header, then load the files lidar by lidar so where and when each one
scanned is known before one is picked on the map.

Files over AUTO_LOAD_MAX_BYTES that aren't cached yet are held back (a
memory and bandwidth guard) and loaded once their lidar is picked.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

import config
from archive.fetchers import clamps_common
from archive.fetchers.raw_lidar_archive_fetcher import (
    KNOWN_RAW_LIDAR_SOURCES, discover_raw_lidar, load_raw_lidar, read_site,
)
from archive.fetchers.vehicle_obs_archive_fetcher import _USER_AGENT
from archive.positions import PositionTrack
from core import package_sources

log = logging.getLogger(__name__)

RAW_LIDAR_CACHE_DIR = Path.home() / ".cache" / "storm" / "raw_lidar"
RAW_LIDAR_CACHE_MAX_BYTES = 3 * 1024 ** 3


def _remote_size(url: str) -> int | None:
    """A file's size from a HEAD request, or None if it can't be told."""
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=20, context=config.NSSL_SSL_CONTEXT) as response:
            length = response.headers.get("Content-Length")
        return int(length) if length else None
    except Exception:  # noqa: BLE001
        return None


class ArchiveRawLidarFetcher(QObject):
    assets_ready = pyqtSignal(dict)            # platform_id -> list[LidarAsset] (None: couldn't check)
    sites_ready = pyqtSignal(dict)             # instrument -> {lat, lon, altitude_m, description} (trailers)
    file_ready = pyqtSignal(str, object)       # instrument, RawLidarRays (with .scans)
    large_file = pyqtSignal(str, object, int)  # instrument, LidarAsset, bytes: held back
    instrument_loaded = pyqtSignal(str, int)   # instrument, files kept
    survey_done = pyqtSignal()
    error = pyqtSignal(str)                    # "<platform or instrument>: <what went wrong>"

    AUTO_LOAD_MAX_BYTES = 150 * 1024 * 1024
    _LOAD_ORDER = {"ppi": 0, "csm": 1}         # PPI files are smaller and show first

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._day = session_date.date()
        self._cancel = threading.Event()
        self._truck_track_source = None
        self._truck_track: PositionTrack | None = None
        self._track_lock = threading.Lock()

    def set_truck_track(self, source) -> None:
        """source() -> the lidar truck's time-ordered observations for the
        session (called on a worker thread, once, when its first file loads)."""
        self._truck_track_source = source

    def cancel(self) -> None:
        self._cancel.set()

    # -- discovery ---------------------------------------------------------------

    def fetch(self) -> None:
        threading.Thread(target=self._do_fetch, daemon=True, name="raw-lidar-find").start()

    def _do_fetch(self) -> None:
        try:
            freed = clamps_common.prune_cache(RAW_LIDAR_CACHE_DIR, RAW_LIDAR_CACHE_MAX_BYTES)
            if freed:
                log.info("Raw lidar cache: removed %.0f MB of least recently used files", freed / 1e6)
        except OSError as exc:
            log.warning("Raw lidar cache not pruned: %s", exc)
        # the session runs into the next UTC morning (archive/session.py),
        # and the lidars' files are daily
        days = (self._day, self._day + timedelta(days=1))
        results: dict[str, list | None] = {}
        errors = []
        for source in KNOWN_RAW_LIDAR_SOURCES:
            assets = []
            try:
                for n, day in enumerate(days):
                    try:
                        assets += discover_raw_lidar(source, day)
                    except Exception:
                        if n == 0:
                            raise
            except Exception as exc:  # noqa: BLE001
                results[source.platform_id] = None
                errors.append(f"{source.platform_id}: {exc}")
                continue
            if assets:
                results[source.platform_id] = assets
        if errors:
            self.error.emit("; ".join(errors))
        self.assets_ready.emit(results)
        # each trailer's site from one file's header, so it's on the map
        # before any file loads (the truck moves: its GPS places it)
        sites = {}
        for source in KNOWN_RAW_LIDAR_SOURCES:
            if source.mobile or source.instrument in sites or not results.get(source.platform_id):
                continue
            site = read_site(results[source.platform_id][0])
            if site is not None:
                sites[source.instrument] = site
        if sites:
            self.sites_ready.emit(sites)

    # -- loading -----------------------------------------------------------------

    def survey(self, assets_by_instrument: dict) -> None:
        """Load every lidar's files, one lidar after another; larger files
        not yet cached are reported with large_file instead."""
        def run():
            for instrument, assets in assets_by_instrument.items():
                self._load_files(instrument, assets, force=False)
            if not self._cancel.is_set():
                self.survey_done.emit()
        threading.Thread(target=run, daemon=True, name="raw-lidar-survey").start()

    def load_files(self, instrument: str, assets) -> None:
        """Load these files of one lidar now, whatever their size."""
        threading.Thread(target=self._load_files, args=(instrument, list(assets), True),
                         daemon=True, name="raw-lidar-load").start()

    def _track(self) -> PositionTrack | None:
        with self._track_lock:
            if self._truck_track is None and self._truck_track_source is not None:
                try:
                    self._truck_track = PositionTrack(self._truck_track_source() or [])
                except Exception as exc:  # noqa: BLE001
                    log.warning("Lidar truck track unavailable: %s", exc)
                    self._truck_track = PositionTrack([])
            return self._truck_track

    def _load_files(self, instrument: str, assets, force: bool) -> None:
        from core.lidar_scans import classify_scans
        kept = 0
        packaged = package_sources.active_package() is not None
        for asset in sorted(assets, key=lambda a: (self._LOAD_ORDER.get(a.source.product, 9), a.filename)):
            if self._cancel.is_set():
                return
            if not (force or packaged or clamps_common.is_cached(asset.url, RAW_LIDAR_CACHE_DIR, asset.filename)):
                size = _remote_size(asset.url)
                if size is not None and size > self.AUTO_LOAD_MAX_BYTES:
                    self.large_file.emit(instrument, asset, size)
                    continue
            try:
                rays = load_raw_lidar(asset, RAW_LIDAR_CACHE_DIR, cancel=self._cancel,
                                      truck_track=self._track() if asset.source.mobile else None)
                rays.scans = classify_scans(rays)
            except clamps_common.Cancelled:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Lidar %s: %s failed to load: %s", instrument, asset.filename, exc)
                self.error.emit(f"{instrument}: {asset.filename}: {exc}")
                continue
            if not rays.scans:
                # only VAD rings or fixed pointing: nothing to draw, so not
                # kept (a day of these held hundreds of MB)
                log.info("Lidar %s: %s has no PPI scans; not kept", instrument, asset.filename)
                continue
            kept += 1
            self.file_ready.emit(instrument, rays)
        self.instrument_loaded.emit(instrument, kept)
