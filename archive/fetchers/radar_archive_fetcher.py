
import atexit
import bisect
import gc
import gzip
import io
import logging
import os
import shutil
import tempfile
import threading
import time as _time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from archive.session import session_bounds, session_days
from core import package_sources, provenance
from core.qt_signals import emit_alive
from core.level2_radar_scan import Level2RadarScan, L2_PRODUCTS, DEFAULT_L2_PRODUCT
from data import endpoints

log = logging.getLogger(__name__)

_S3_BASE = endpoints.NEXRAD_LEVEL2_BUCKET
_S3_NS   = "http://s3.amazonaws.com/doc/2006-03-01/"

# maps L2_PRODUCTS keys → MetPy Level2File moment name (plain ASCII string).
_MOMENT_MAP: dict[str, str] = {
    "reflectivity":              "REF",
    "velocity":                  "VEL",
    "spectrum_width":            "SW",
    "differential_reflectivity": "ZDR",
    "cross_correlation_ratio":   "RHO",
    "differential_phase":        "PHI",
    "specific_differential_phase": "KDP",
    "clutter_filter_power_removed": "CFP",
}


@dataclass(frozen=True)
class VelocityOptions:
    """Optional velocity processing (core/velocity_processing.py). Part of
    each velocity decode request and cache key, so switching back reuses
    earlier results and a result can't be shown under the wrong options."""
    dealias: bool = False

    def __bool__(self) -> bool:
        return self.dealias


# stations not archived in the Unidata/NOAA Level-2 S3 bucket.
ARCHIVE_UNAVAILABLE_STATIONS: frozenset[str] = frozenset({
    "KOUN",   # NSSL research WSR-88D, Norman OK — not in public archive
})

BUFFER_BEFORE = 4
BUFFER_AFTER  = 4
# concurrent downloads; parsing is serialized separately (see _parse_lock)
FETCH_WORKERS = 2
# the displayed scan's parse (465-840 MB) is let go after this long unused;
# a later product/tilt switch re-parses it from disk (1-4 s)
PARSED_IDLE_S = 45.0
# key suffixes to guess when the listing has no exact key; pre-2016 objects
# are gzip-compressed under ".gz" keys
_KEY_SUFFIXES = ("_V06", "_V03", "", "_V06.gz", "_V03.gz", ".gz")


class ArchiveRadarFetcher(QObject):
    """
    Fetches and decodes NEXRAD Level-2 archive data for a single station.

    Signals
    -------
    scan_ready(Level2RadarScan)
        Emitted on the main thread when the scan for the current archive time
        has been decoded and is ready to render.
    index_loaded(list[str])
        Emitted once the scan-time index for the session date has been fetched.
        Payload is a list of ISO-format scan time strings.
    loading_changed(bool)
        True while a file is being downloaded or decoded.
    error(str)
        Human-readable error message.
    """

    scan_ready      = pyqtSignal(object)   # Level2RadarScan
    index_loaded    = pyqtSignal(list)     # list[str] scan time ISO strings
    loading_changed = pyqtSignal(bool)
    error           = pyqtSignal(str)

    def __init__(self, station: str, session_date: datetime, parent=None):
        """
        Parameters
        ----------
        station : str
            4-letter NEXRAD ID, e.g. "KTLX".
        session_date : datetime
            UTC datetime of the archive session (only the date portion is used).
        """
        super().__init__(parent)
        # set by shutdown(); workers check it and _emit drops signals after it
        self._closed = threading.Event()
        self._signal_lock = threading.Lock()
        self._station     = station.upper()
        self._date        = session_date
        self._product     = DEFAULT_L2_PRODUCT
        self._tilt_idx    = 0          # index into available tilts list
        self._velocity    = VelocityOptions()

        # raw Level-2 files are written to a per-session tmp directory (~30 MB
        self._tmpdir = tempfile.mkdtemp(prefix="storm_radar_")
        atexit.register(shutil.rmtree, self._tmpdir, True)

        # maps scan_time → path of the raw .dat file in _tmpdir (None = fetch pending).
        self._raw_cache: dict[datetime, Optional[str]] = {}
        # parsed metpy Level2File for the displayed scan only, so switching
        # product/tilt on it doesn't re-parse.  A parsed volume holds ~0.5 GB,
        # so neighbours are parsed, decoded and dropped.
        self._parsed_cache: dict[datetime, object] = {}
        # one parse at a time bounds peak memory to ~2 parsed volumes
        self._parse_lock = threading.Lock()
        self._parsed_used = _time.monotonic()
        threading.Thread(
            target=self._release_idle_parse, daemon=True, name="archive-radar-reaper"
        ).start()
        self._pool = ThreadPoolExecutor(
            max_workers=FETCH_WORKERS, thread_name_prefix="archive-radar"
        )
        self._decoded_cache: dict[tuple, Level2RadarScan] = {}
        # sorted list of all known scan times for the date.
        self._index: list[datetime] = []
        # scan_time → position in _index
        self._index_pos: dict[datetime, int] = {}
        # scan_time → exact S3 object key from the listing
        self._index_keys: dict[datetime, str] = {}
        self._index_lock = threading.Lock()
        self._index_loading = False      # a listing is under way (load_index → _fetch_index)

        self._current_archive_time: Optional[datetime] = None
        self._last_emitted_key: Optional[tuple] = None  # suppress re-render of same scan
        # re-entrant: cancelling a queued future runs its done-callback
        # (_forget_work, which takes this lock) in the cancelling thread
        self._fetch_lock = threading.RLock()
        self._pending_fetches: set[datetime] = set()
        self._pending_decodes: set[tuple] = set()
        # queued work that may not have started, so a seek can drop what's
        # stale: ("fetch", scan_time) or ("decode", cache_key) → Future
        self._queued: dict[tuple, object] = {}
        # the scan the clock wants on screen
        self._target_scan: Optional[datetime] = None
        # off (set_active): the clock fetches and decodes nothing
        self._active = True


    @property
    def station(self) -> str:
        return self._station

    def _emit(self, signal: str, *args) -> None:
        # serialized with shutdown(), so nothing is delivered after close; by
        # name, since reading a signal off a deleted fetcher already raises
        with self._signal_lock:
            if not self._closed.is_set():
                emit_alive(self, signal, *args)

    def shutdown(self) -> None:
        """Stop all work and free the volumes before this fetcher is replaced.

        Queued work is cancelled; running downloads finish but emit nothing.
        The temp folder is removed off the GUI thread once they're done.
        """
        with self._signal_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        # not under _parse_lock: a running parse would block the GUI thread
        self._parsed_cache.clear()
        self._decoded_cache.clear()
        with self._fetch_lock:
            self._pending_fetches.clear()
            self._pending_decodes.clear()
            self._queued.clear()

        def finish():
            self._pool.shutdown(wait=True)
            shutil.rmtree(self._tmpdir, ignore_errors=True)

        threading.Thread(target=finish, daemon=True, name="archive-radar-cleanup").start()

    def set_station(self, station: str) -> None:
        self._station = station.upper()
        for path in self._raw_cache.values():
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._raw_cache.clear()
        self._parsed_cache.clear()
        self._decoded_cache.clear()
        with self._index_lock:
            self._index = []
            self._index_pos = {}
            self._index_keys = {}
        self._target_scan = None
        self._pending_fetches.clear()
        self._pending_decodes.clear()
        self.load_index()

    def set_product(self, pyart_field: str) -> None:
        """Switch the rendered product and reuse cached raw/decoded volumes."""
        if pyart_field == self._product:
            return
        self._product = pyart_field
        self._last_emitted_key = None
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def set_velocity_options(self, options: VelocityOptions) -> None:
        """Dealias velocity or not; raw velocity stays cached."""
        if options == self._velocity:
            return
        self._velocity = options
        if self._product == "velocity":
            self._last_emitted_key = None
            if self._current_archive_time is not None:
                self.on_time_changed(self._current_archive_time)

    def set_tilt_index(self, idx: int) -> None:
        """Switch the elevation tilt and reuse cached raw/decoded volumes."""
        self._tilt_idx = idx
        self._last_emitted_key = None
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def set_active(self, active: bool) -> None:
        """Off: drop queued downloads and decodes and follow the clock no
        further (the listing still loads, for the timeline). On: catch up
        with the clock."""
        if active == self._active:
            return
        self._active = active
        if not active:
            self._target_scan = None
            self._drop_stale_work(None)
            # the kept parse is ~0.5 GB; a decode under way still finishes
            self._parsed_cache = {}
            return
        self._last_emitted_key = None
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def load_index(self) -> None:
        """Fetch the list of available scans from AWS (background thread)."""
        if self._closed.is_set():
            return
        self._index_loading = True
        t = threading.Thread(target=self._fetch_index, daemon=True)
        t.start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """
        Called by TimeController whenever the archive clock advances.
        Finds the correct scan for the given time, emits it if cached,
        or triggers a background fetch if not.
        """
        if self._closed.is_set():
            return
        self._current_archive_time = archive_time
        if not self._active:
            return
        scan_time = self._nearest_scan_before(archive_time)
        if scan_time is None:
            return
        if scan_time != self._target_scan:
            self._target_scan = scan_time
            self._drop_stale_work(scan_time)
        cache_key = self._decode_key(scan_time)
        scan = self._decoded_cache.get(cache_key)
        if scan is not None:
            if cache_key != self._last_emitted_key:
                self._last_emitted_key = cache_key
                self._emit("scan_ready", scan)
        elif self._raw_cache.get(scan_time) is not None:
            self._ensure_decoded(scan_time)
        else:
            self._ensure_fetched(scan_time)

        # maintain buffer: pre-fetch neighbouring scans.
        self._maintain_buffer(scan_time)


    def _fetch_index(self) -> None:
        """List the station's files for the session's span from the AWS S3
        bucket: its UTC day and the next morning (archive/session.py)."""
        start, cap = session_bounds(self._date)
        try:
            keys: dict[datetime, str] = {}
            for day in session_days(self._date):
                prefix = f"{day.strftime('%Y/%m/%d')}/{self._station}/"
                url = f"{_S3_BASE}/?prefix={prefix}&list-type=2"
                resp = package_sources.requests_get("radar", url, timeout=15)
                resp.raise_for_status()
                keys.update({t: k for t, k in self._parse_s3_listing(resp.text).items()
                             if start <= t <= cap})
            times = list(keys)
            with self._index_lock:
                self._index = sorted(times)
                self._index_pos = {t: i for i, t in enumerate(self._index)}
                self._index_keys = keys
            log.info(
                "ArchiveRadarFetcher: %d scans indexed for %s on %s",
                len(self._index), self._station, self._date.strftime("%Y-%m-%d"),
            )
            if not times:
                self._emit(
                    "error",
                    f"No archive data found for {self._station} on "
                    f"{self._date.strftime('%Y-%m-%d')} — "
                    f"station may not be in the public archive"
                )
                return
            self._emit(
                "index_loaded",
                [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in self._index]
            )
            # pre-fetch the scan nearest to the current archive time.
            if self._current_archive_time and self._index:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: index fetch failed: %s", exc)
            self._emit("error", f"Radar index failed for {self._station}: {exc}")
        finally:
            self._index_loading = False

    def _parse_s3_listing(self, xml_text: str) -> dict[datetime, str]:
        """Parse S3 XML listing into scan datetime → exact object key."""
        times: dict[datetime, str] = {}
        try:
            root = ET.fromstring(xml_text)
            ns = _S3_NS
            for content in root.findall(f"{{{ns}}}Contents"):
                key = content.findtext(f"{{{ns}}}Key", "")
                # filename pattern: KXXX20230515_201500_V06 (and variants)
                fname = key.split("/")[-1]
                if not fname or "MDM" in fname:
                    continue
                dt = _parse_l2_filename_time(fname, self._station)
                if dt:
                    times[dt] = key
        except Exception as exc:
            log.warning("ArchiveRadarFetcher: S3 parse error: %s", exc)
        return times


    def next_scan_after(self, t: datetime) -> Optional[datetime]:
        """The first indexed scan time after t, if any."""
        with self._index_lock:
            idx = self._index
        i = bisect.bisect_right(idx, t)
        return idx[i] if i < len(idx) else None

    @property
    def index_loading(self) -> bool:
        """The station's scan listing is still being fetched."""
        return self._index_loading

    def scan_times(self) -> list[datetime]:
        """Every listed scan time, oldest first."""
        with self._index_lock:
            return list(self._index)

    def _nearest_scan_before(self, t: datetime) -> Optional[datetime]:
        """Return the latest scan_time <= t from the index."""
        with self._index_lock:
            idx = self._index
        if not idx:
            return None
        # binary search for the last element <= t.
        lo, hi = 0, len(idx) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if idx[mid] <= t:
                result = idx[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _ensure_fetched(self, scan_time: datetime) -> None:
        """Kick off a background raw-file fetch for scan_time if needed."""
        with self._fetch_lock:
            if scan_time in self._pending_fetches:
                return
            if self._raw_cache.get(scan_time) is not None:
                return
            if self._closed.is_set():
                return
            self._pending_fetches.add(scan_time)
            self._raw_cache[scan_time] = None

        self._emit("loading_changed", True)
        self._submit(("fetch", scan_time), self._fetch_raw_then_decode, scan_time)

    def _ensure_decoded(self, scan_time: datetime) -> None:
        cache_key = self._decode_key(scan_time)
        with self._fetch_lock:
            if cache_key in self._pending_decodes or cache_key in self._decoded_cache:
                return
            if self._raw_cache.get(scan_time) is None:
                return
            if self._closed.is_set():
                return
            self._pending_decodes.add(cache_key)
        self._emit("loading_changed", True)
        self._submit(("decode", cache_key), self._decode_cached_scan, scan_time, cache_key)

    def _submit(self, work_key: tuple, task, *args) -> None:
        try:
            future = self._pool.submit(task, *args)
        except RuntimeError:
            # pool already shut down
            if not self._closed.is_set():
                raise
            return
        with self._fetch_lock:
            if not future.done():
                self._queued[work_key] = future
        future.add_done_callback(lambda f, k=work_key: self._forget_work(k, f))

    def _forget_work(self, work_key: tuple, future) -> None:
        with self._fetch_lock:
            if self._queued.get(work_key) is future:
                del self._queued[work_key]

    def _drop_stale_work(self, scan_time: datetime) -> None:
        """After a seek, cancel queued work for every scan but the one now
        wanted, so it isn't stuck behind the old position's downloads.
        Running work finishes; the buffer is re-queued around the new
        position once the wanted scan's file is in."""
        with self._fetch_lock:
            for work_key, future in list(self._queued.items()):
                kind, what = work_key
                target = what if kind == "fetch" else what[0]
                if target == scan_time or not future.cancel():
                    continue
                self._queued.pop(work_key, None)
                if kind == "fetch":
                    self._pending_fetches.discard(target)
                    if target in self._raw_cache and self._raw_cache[target] is None:
                        del self._raw_cache[target]
                else:
                    self._pending_decodes.discard(what)
            self._update_loading_state()

    def _fetch_raw_then_decode(self, scan_time: datetime) -> None:
        cache_key = self._decode_key(scan_time)
        decoding = False
        try:
            file_bytes = self._download_scan(scan_time)
            if file_bytes is None or self._closed.is_set():
                return
            # persist raw bytes to tmp file so later product/tilt switches
            # (after this call returns) can still find the raw scan on disk.
            path = os.path.join(
                self._tmpdir,
                f"{self._station}_{scan_time.strftime('%Y%m%d_%H%M%S')}.dat",
            )
            with open(path, "wb") as fh:
                fh.write(file_bytes)
            self._raw_cache[scan_time] = path
            if scan_time == self._target_scan:
                # the wanted scan is in: now fetch its neighbours
                self._maintain_buffer(scan_time)

            with self._fetch_lock:
                if cache_key in self._decoded_cache or cache_key in self._pending_decodes:
                    return
                self._pending_decodes.add(cache_key)
                decoding = True
            # decode the bytes already in memory instead of re-reading them
            # back from the file we just wrote.
            self._decode_and_cache(scan_time, file_bytes, cache_key)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: fetch failed for %s: %s", scan_time, exc)
            self._emit("error", f"Radar fetch error: {exc}")
        finally:
            with self._fetch_lock:
                self._pending_fetches.discard(scan_time)
                if decoding:
                    self._pending_decodes.discard(cache_key)
                self._update_loading_state()

    def _decode_cached_scan(self, scan_time: datetime, cache_key: tuple) -> None:
        try:
            path = self._raw_cache.get(scan_time)
            if not path or not os.path.exists(path):
                return
            with open(path, "rb") as fh:
                file_bytes = fh.read()
            self._decode_and_cache(scan_time, file_bytes, cache_key)
        finally:
            with self._fetch_lock:
                self._pending_decodes.discard(cache_key)
                self._update_loading_state()

    def _decode_and_cache(self, scan_time: datetime, file_bytes: bytes, cache_key: tuple) -> None:
        try:
            if self._closed.is_set():
                return
            # decode what was asked for, even if the product or tilt changes
            # while the parse runs
            _, product, tilt_idx, velocity = cache_key
            scan = self._decode(
                scan_time, file_bytes, product=product, tilt_idx=tilt_idx, velocity=velocity,
            )
            if self._closed.is_set():
                return
            self._decoded_cache[cache_key] = scan
            if (
                self._active
                and self._current_archive_time is not None
                and self._nearest_scan_before(self._current_archive_time) == scan_time
                and self._decode_key(scan_time) == cache_key
            ):
                self._emit("scan_ready", scan)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: decode failed for %s: %s", scan_time, exc)
            self._emit("error", f"Radar decode error: {exc}")

    def _update_loading_state(self) -> None:
        if not self._pending_fetches and not self._pending_decodes:
            self._emit("loading_changed", False)

    def _decode_key(self, scan_time: datetime) -> tuple:
        """(scan time, product, tilt, velocity options or None)."""
        velocity = self._velocity if self._product == "velocity" and self._velocity else None
        return (scan_time, self._product, self._tilt_idx, velocity)

    def _download_scan(self, scan_time: datetime) -> Optional[bytes]:
        """Fetch the raw Level-2 file bytes from AWS S3, gunzipped if needed."""
        prefix = (
            f"{scan_time.strftime('%Y/%m/%d')}/{self._station}/"
            f"{self._station}{scan_time.strftime('%Y%m%d_%H%M%S')}"
        )
        # the listing's exact key first; guessing costs a round trip per miss
        with self._index_lock:
            listed = self._index_keys.get(scan_time)
        urls = [f"{_S3_BASE}/{listed}"] if listed else []
        urls += [
            u for u in (f"{_S3_BASE}/{prefix}{suffix}" for suffix in _KEY_SUFFIXES)
            if u not in urls
        ]
        for url in urls:                 # an opened case package's copy first, no network
            data = package_sources.get(url)
            if data is not None:
                return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        for url in urls:
            if self._closed.is_set():
                return None
            try:
                resp = requests.get(url, timeout=60, stream=True)
                if resp.status_code == 200:
                    data = resp.content
                    provenance.record("radar", url, data)
                    log.debug(
                        "ArchiveRadarFetcher: downloaded %s (%.1f MB)",
                        url.split("/")[-1], len(data) / 1e6,
                    )
                    # S3 sets no Content-Encoding on these, so check the magic bytes
                    if data[:2] == b"\x1f\x8b":
                        data = gzip.decompress(data)
                    return data
            except Exception:
                continue
        log.warning("ArchiveRadarFetcher: could not download scan %s", scan_time)
        return None

    def _release_idle_parse(self) -> None:
        """Drop the kept parse once it has sat unused for PARSED_IDLE_S."""
        while not self._closed.wait(5.0):
            if self._parsed_cache and _time.monotonic() - self._parsed_used > PARSED_IDLE_S:
                with self._parse_lock:
                    if _time.monotonic() - self._parsed_used > PARSED_IDLE_S:
                        self._parsed_cache.clear()
                # MetPy's parse holds reference cycles
                gc.collect()

    def _get_parsed(self, scan_time: datetime, file_bytes: bytes):
        """Parse the raw Level-2 bytes with MetPy, reusing a cached parse.

        Parsing is independent of the selected product/tilt, so a scan that
        was already fetched only needs to be parsed once even if the user
        switches products or tilts repeatedly.
        """
        self._parsed_used = _time.monotonic()
        f = self._parsed_cache.get(scan_time)
        if f is None:
            from metpy.io import Level2File
            f = Level2File(io.BytesIO(file_bytes))
            if self._is_displayed_scan(scan_time):
                self._parsed_cache = {scan_time: f}
        return f

    def _is_displayed_scan(self, scan_time: datetime) -> bool:
        return scan_time == self._target_scan

    def _decode(
        self, scan_time: datetime, file_bytes: bytes, *,
        product: Optional[str] = None, tilt_idx: Optional[int] = None,
        velocity: Optional[VelocityOptions] = None,
    ) -> Optional[Level2RadarScan]:
        """Decode a Level-2 file with MetPy and return a Level2RadarScan."""
        with self._parse_lock:
            return self._decode_parsed(
                scan_time, self._get_parsed(scan_time, file_bytes),
                self._product if product is None else product,
                self._tilt_idx if tilt_idx is None else tilt_idx,
                velocity,
            )

    def _decode_parsed(
        self, scan_time: datetime, f, requested_product: str, requested_tilt: int,
        velocity: Optional[VelocityOptions] = None,
    ) -> Optional[Level2RadarScan]:
        if not f.sweeps:
            raise RuntimeError("No sweeps found in Level-2 file")

        sweeps = [sweep for sweep in f.sweeps if sweep]
        if not sweeps:
            raise RuntimeError("No valid sweeps in Level-2 file")

        # the VCP's intended angle per cut: the first ray's measured angle
        # wobbles (0.6 / 0.5 for the two 0.48° cuts), which listed
        # near-duplicate tilts
        cuts = getattr(getattr(f, "vcp_info", None), "els", None) or []
        available_tilts = []
        sweep_products: list[set[str]] = []
        for sweep in sweeps:
            rad0 = sweep[0]
            # msg31: Radial namedtuple with .header; MSG1: plain (hdr, data) tuple
            hdr = rad0.header if hasattr(rad0, "header") else rad0[0]
            cut = getattr(hdr, "el_num", 0)
            angle = cuts[cut - 1].el_angle if 0 < cut <= len(cuts) else hdr.el_angle
            available_tilts.append(round(float(angle), 1))
            present: set[str] = set()
            for rad in sweep:
                moments = rad.moments if hasattr(rad, "moments") else rad[1]
                present.update(
                    (k.decode() if isinstance(k, bytes) else k).strip() for k in moments.keys()
                )
            sweep_products.append({p for p in L2_PRODUCTS if _MOMENT_MAP[p] in present})

        available_products = [
            p for p in L2_PRODUCTS if any(p in fields for fields in sweep_products)
        ]
        # split cuts carry reflectivity and velocity in separate sweeps at one
        # angle: use the nearest sweep that has the product
        compatible = [i for i, fields in enumerate(sweep_products) if requested_product in fields]
        if not compatible:
            raise RuntimeError(f"No {requested_product} data in this radar volume")
        preferred = max(0, min(requested_tilt, len(sweeps) - 1))
        tilt_idx = min(
            compatible,
            key=lambda i: (abs(available_tilts[i] - available_tilts[preferred]), abs(i - preferred)),
        )
        tilt_deg = available_tilts[tilt_idx]
        sweep    = sweeps[tilt_idx]
        is_msg31 = hasattr(sweep[0], "moments")
        product  = requested_product
        moment   = _MOMENT_MAP[product]

        radar_lat = radar_lon = None
        for rad in sweep:
            vc = getattr(rad, "vol_consts", None)
            if vc is not None:
                radar_lat = float(vc.lat)
                radar_lon = float(vc.lon)
                break
        if radar_lat is None:
            # msg1 files don't carry lat/lon — fall back to NEXRAD_SITES table.
            from ui.controls.radar_controls import NEXRAD_SITES
            for sid, _name, lat, lon in NEXRAD_SITES:
                if sid == self._station:
                    radar_lat, radar_lon = lat, lon
                    break
        if radar_lat is None:
            raise RuntimeError(f"Cannot determine radar location for {self._station}")

        azimuths  = []
        gate_rows = []
        first_gate_km = gate_width_km = None

        for rad in sweep:
            hdr     = rad.header if is_msg31 else rad[0]
            moments = rad.moments if is_msg31 else rad[1]

            # metPy MSG31 keys are bytes (b'REF'), MSG1 keys are str ('REF').
            entry = moments.get(moment.encode(), moments.get(moment))
            if entry is None:
                continue

            data_hdr, vals = entry
            if first_gate_km is None:
                first_gate_km = float(data_hdr.first_gate)   # km
                gate_width_km = float(data_hdr.gate_width)   # km

            azimuths.append(float(hdr.az_angle))
            gate_rows.append(vals)

        if not azimuths:
            raise RuntimeError(f"No {moment} data found in sweep {tilt_idx}")

        # metPy returns radials in transmission order (first az often ~180-200°).
        sort_idx = np.argsort(azimuths)
        azimuths  = [azimuths[i] for i in sort_idx]
        gate_rows = [gate_rows[i] for i in sort_idx]

        n_rays  = len(azimuths)
        n_gates = max(len(r) for r in gate_rows)
        data = np.full((n_rays, n_gates), np.nan, dtype=np.float32)
        for i, row in enumerate(gate_rows):
            ng = len(row)
            data[i, :ng] = row  # already NaN for MISSING/RANGE_FOLD

        processing = ""
        if product == "velocity" and velocity:
            data, processing = _process_velocity(data, sweep, velocity)

        # velocity and spectrum width are in m/s from MetPy — convert to knots.
        if product in ("velocity", "spectrum_width"):
            data *= 1.94384

        meta = L2_PRODUCTS[product]
        scan = Level2RadarScan(
            site=self._station,
            product=meta["label"].split("(")[-1].rstrip(")"),
            scan_time=scan_time,
            # half precision is ample for display (~0.01 at these magnitudes)
            raw=data.astype(np.float16),
            vmin=meta["vmin"],
            vmax=meta["vmax"],
            units=meta["units"],
            colormap=meta["colormap"],
            radar_lat=radar_lat,
            radar_lon=radar_lon,
            azimuths_deg=azimuths,
            first_gate_m=first_gate_km * 1000.0,
            gate_width_m=gate_width_km * 1000.0,
            tilt_deg=tilt_deg,
            available_tilts=available_tilts,
            available_products=available_products,
            pyart_field=product,
        )
        # the sweep actually shown, so the tilt picker follows a split-cut swap
        scan.tilt_index = tilt_idx
        # what was done to the velocity ("" when nothing was asked for)
        scan.velocity_processing = processing
        return scan


    def _maintain_buffer(self, current_scan_time: datetime) -> None:
        """Pre-fetch BUFFER_BEFORE + BUFFER_AFTER scans around current_scan_time."""
        with self._index_lock:
            idx = self._index
            index_pos = self._index_pos
        pos = index_pos.get(current_scan_time)
        if pos is None:
            return
        if self._raw_cache.get(current_scan_time) is None:
            # the wanted scan alone first: a neighbour downloading alongside
            # halves its speed on a slow link. _fetch_raw_then_decode calls
            # back here once it is in.
            return
        # nearest first, upcoming scans before earlier ones, so playback
        # reaches a ready frame sooner (the pool runs jobs in submit order)
        order = [pos]
        for step in range(1, max(BUFFER_BEFORE, BUFFER_AFTER) + 1):
            if step <= BUFFER_AFTER and pos + step < len(idx):
                order.append(pos + step)
            if step <= BUFFER_BEFORE and pos - step >= 0:
                order.append(pos - step)
        for i in order:
            t = idx[i]
            if self._raw_cache.get(t) is None and t not in self._pending_fetches:
                self._ensure_fetched(t)

        # the parsed volume is only kept for the displayed scan
        for t in list(self._parsed_cache):
            if t != current_scan_time:
                self._parsed_cache.pop(t, None)

        # evict scans far outside the buffer to keep disk and memory manageable.
        evict_lo = pos - BUFFER_BEFORE - 4
        evict_hi = pos + BUFFER_AFTER + 4
        for t in list(self._raw_cache.keys()):
            i = index_pos.get(t, -1)
            if i == -1 or i < evict_lo or i > evict_hi:
                path = self._raw_cache.pop(t, None)
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        # decoded scans by time too: ones whose raw file went in an earlier
        # seek were never reached above and piled up (~16 MB each)
        lo, hi = max(0, evict_lo), min(len(idx) - 1, evict_hi)
        keep = set(idx[lo:hi + 1])
        for key in [k for k in list(self._decoded_cache) if k[0] not in keep]:
            self._decoded_cache.pop(key, None)



def _process_velocity(data_ms, sweep, options: VelocityOptions) -> tuple[np.ndarray, str]:
    """Apply the requested velocity processing to one sweep (m/s), returning
    the field and what was actually done. The raw field comes back, labeled
    raw, when it can't be dealiased."""
    from core.velocity_processing import dealias, sweep_nyquist
    nyquist = sweep_nyquist(sweep)
    if nyquist is None:
        return data_ms, "raw: no Nyquist velocity recorded, not dealiased"
    try:
        return dealias(data_ms, nyquist), f"dealiased (region-based, Nyquist {nyquist:.1f} m/s)"
    except Exception as exc:
        log.warning("ArchiveRadarFetcher: dealiasing failed, showing raw velocity: %s", exc)
        return data_ms, "raw: dealiasing failed"


def _parse_l2_filename_time(fname: str, station: str) -> Optional[datetime]:
    """
    Extract UTC datetime from a Level-2 filename.
    Expected pattern: KXXX20230515_201500_V06
    """
    try:
        # strip station prefix (4 chars).
        rest = fname[4:]
        # rest = "20230515_201500_V06"
        date_part, rest2 = rest.split("_", 1)
        time_part = rest2.split("_")[0]
        dt_str = f"{date_part}{time_part}"
        return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception as exc:
        log.debug("ArchiveRadarFetcher: could not parse filename %r: %s", fname, exc)
        return None
