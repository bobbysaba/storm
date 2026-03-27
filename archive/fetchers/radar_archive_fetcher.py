# archive/fetchers/radar_archive_fetcher.py
# ArchiveRadarFetcher — downloads NEXRAD Level-2 files from the AWS public S3
# bucket and decodes them with pyart.
#
# AWS bucket:  s3://noaa-nexrad-level2 (public, no auth required)
# HTTP listing: https://noaa-nexrad-level2.s3.amazonaws.com/?prefix=YYYY/MM/DD/KXXX/
# File URL:     https://noaa-nexrad-level2.s3.amazonaws.com/YYYY/MM/DD/KXXX/<filename>
#
# Buffer strategy
# ---------------
# Maintains a sliding window of BUFFER_BEFORE scans before and BUFFER_AFTER
# scans after the current archive time.  Pre-fetches missing slots in a
# background thread so the UI stays responsive during playback.

import io
import logging
import tempfile
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from core.level2_radar_scan import Level2RadarScan, L2_PRODUCTS, DEFAULT_L2_PRODUCT

log = logging.getLogger(__name__)

_S3_BASE = "https://noaa-nexrad-level2.s3.amazonaws.com"
_S3_NS   = "http://s3.amazonaws.com/doc/2006-03-01/"

BUFFER_BEFORE = 4
BUFFER_AFTER  = 4


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
        self._station     = station.upper()
        self._date        = session_date
        self._product     = DEFAULT_L2_PRODUCT
        self._tilt_idx    = 0          # index into available tilts list

        # {scan_time_utc: Level2RadarScan | None}  — None = fetching in progress
        self._cache: dict[datetime, Optional[Level2RadarScan]] = {}
        # Sorted list of all known scan times for the date.
        self._index: list[datetime] = []
        self._index_lock = threading.Lock()

        self._current_archive_time: Optional[datetime] = None
        self._fetch_lock = threading.Lock()
        self._pending_fetches: set[datetime] = set()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def station(self) -> str:
        return self._station

    def set_station(self, station: str) -> None:
        self._station = station.upper()
        self._cache.clear()
        self._index = []
        self._pending_fetches.clear()
        self.load_index()

    def set_product(self, pyart_field: str) -> None:
        """Switch the rendered product; clears decoded cache (raw files stay)."""
        if pyart_field == self._product:
            return
        self._product = pyart_field
        # Clear decoded scans so they are re-decoded with the new product.
        self._cache = {t: None for t in self._cache}
        self._pending_fetches.clear()
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def set_tilt_index(self, idx: int) -> None:
        """Switch the elevation tilt; triggers a re-decode of the current scan."""
        self._tilt_idx = idx
        self._cache = {t: None for t in self._cache}
        self._pending_fetches.clear()
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def load_index(self) -> None:
        """Fetch the list of available scans from AWS (background thread)."""
        t = threading.Thread(target=self._fetch_index, daemon=True)
        t.start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """
        Called by TimeController whenever the archive clock advances.
        Finds the correct scan for the given time, emits it if cached,
        or triggers a background fetch if not.
        """
        self._current_archive_time = archive_time
        scan_time = self._nearest_scan_before(archive_time)
        if scan_time is None:
            return
        scan = self._cache.get(scan_time)
        if scan is not None:
            self.scan_ready.emit(scan)
        else:
            self._ensure_fetched(scan_time)

        # Maintain buffer: pre-fetch neighbouring scans.
        self._maintain_buffer(scan_time)

    # ── Index loading ─────────────────────────────────────────────────────────

    def _fetch_index(self) -> None:
        """List all files for the station/date from the AWS S3 bucket."""
        prefix = (
            f"{self._date.strftime('%Y/%m/%d')}/{self._station}/"
        )
        url = f"{_S3_BASE}/?prefix={prefix}&list-type=2"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            times = self._parse_s3_listing(resp.text)
            with self._index_lock:
                self._index = sorted(times)
            log.info(
                "ArchiveRadarFetcher: %d scans indexed for %s on %s",
                len(self._index), self._station, self._date.strftime("%Y-%m-%d"),
            )
            self.index_loaded.emit(
                [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in self._index]
            )
            # Pre-fetch the scan nearest to midnight (or any initial time).
            if self._current_archive_time and self._index:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: index fetch failed: %s", exc)
            self.error.emit(f"Radar index failed for {self._station}: {exc}")

    def _parse_s3_listing(self, xml_text: str) -> list:
        """Parse S3 XML listing and extract scan datetimes."""
        times = []
        try:
            root = ET.fromstring(xml_text)
            ns = _S3_NS
            for content in root.findall(f"{{{ns}}}Contents"):
                key = content.findtext(f"{{{ns}}}Key", "")
                # Filename pattern: KXXX20230515_201500_V06 (and variants)
                fname = key.split("/")[-1]
                if not fname or "MDM" in fname:
                    continue
                dt = _parse_l2_filename_time(fname, self._station)
                if dt:
                    times.append(dt)
        except Exception as exc:
            log.warning("ArchiveRadarFetcher: S3 parse error: %s", exc)
        return times

    # ── Scan fetching ─────────────────────────────────────────────────────────

    def _nearest_scan_before(self, t: datetime) -> Optional[datetime]:
        """Return the latest scan_time <= t from the index."""
        with self._index_lock:
            idx = self._index
        if not idx:
            return None
        # Binary search for the last element <= t.
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
        """Kick off a background fetch for scan_time if not already in progress."""
        with self._fetch_lock:
            if scan_time in self._pending_fetches:
                return
            if self._cache.get(scan_time) is not None:
                return
            self._pending_fetches.add(scan_time)
            self._cache[scan_time] = None

        self.loading_changed.emit(True)
        t = threading.Thread(
            target=self._fetch_and_decode, args=(scan_time,), daemon=True
        )
        t.start()

    def _fetch_and_decode(self, scan_time: datetime) -> None:
        try:
            file_bytes = self._download_scan(scan_time)
            if file_bytes is None:
                return
            scan = self._decode(scan_time, file_bytes)
            self._cache[scan_time] = scan
            with self._fetch_lock:
                self._pending_fetches.discard(scan_time)
            # If this is still the scan the user should see, emit it.
            if (
                self._current_archive_time is not None
                and self._nearest_scan_before(self._current_archive_time) == scan_time
            ):
                self.scan_ready.emit(scan)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: decode failed for %s: %s", scan_time, exc)
            self.error.emit(f"Radar decode error: {exc}")
        finally:
            with self._fetch_lock:
                self._pending_fetches.discard(scan_time)
            # Emit loading_changed(False) only when no more fetches are pending.
            with self._fetch_lock:
                if not self._pending_fetches:
                    self.loading_changed.emit(False)

    def _download_scan(self, scan_time: datetime) -> Optional[bytes]:
        """Fetch the raw Level-2 file bytes from AWS S3."""
        with self._index_lock:
            idx = self._index
        # Find a matching filename in the index for this exact scan time.
        # We look for the file whose parsed time matches scan_time.
        prefix = (
            f"{scan_time.strftime('%Y/%m/%d')}/{self._station}/"
            f"{self._station}{scan_time.strftime('%Y%m%d_%H%M%S')}"
        )
        # Try V06 first, then V03.
        for suffix in ("_V06", "_V03", ""):
            url = f"{_S3_BASE}/{prefix}{suffix}"
            try:
                resp = requests.get(url, timeout=60, stream=True)
                if resp.status_code == 200:
                    data = resp.content
                    log.debug(
                        "ArchiveRadarFetcher: downloaded %s (%.1f MB)",
                        url.split("/")[-1], len(data) / 1e6,
                    )
                    return data
            except Exception:
                continue
        log.warning("ArchiveRadarFetcher: could not download scan %s", scan_time)
        return None

    def _decode(self, scan_time: datetime, file_bytes: bytes) -> Optional[Level2RadarScan]:
        """Decode a Level-2 file with pyart and return a Level2RadarScan."""
        try:
            import pyart
        except ImportError:
            raise RuntimeError(
                "pyart is not installed.  Run: conda install -c conda-forge arm_pyart"
            )

        # Write to a temp file because pyart wants a file path.
        with tempfile.NamedTemporaryFile(suffix=".ar2v", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            radar = pyart.io.read_nexrad_archive(tmp_path, delay_field_loading=True)
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        # Gather available tilts (elevation angles per sweep).
        available_tilts = [
            float(radar.fixed_angle["data"][i])
            for i in range(radar.nsweeps)
        ]

        # Gather available products (fields present in at least the first sweep).
        available_products = [
            f for f in L2_PRODUCTS
            if f in radar.fields
        ]
        if not available_products:
            raise RuntimeError("No recognised fields in Level-2 file")

        # Pick the requested product; fall back to reflectivity.
        product = self._product if self._product in available_products else available_products[0]

        # Pick the tilt; clamp to valid range.
        tilt_idx = min(self._tilt_idx, radar.nsweeps - 1)
        tilt_deg = available_tilts[tilt_idx]

        # Extract the sweep.
        sweep = radar.extract_sweeps([tilt_idx])
        gate_lat, gate_lon, _ = sweep.get_gate_lat_lon_alt(0)

        raw = sweep.fields[product]["data"]
        data = np.ma.filled(raw, np.nan).astype(np.float32)

        meta = L2_PRODUCTS[product]

        # Velocity data from pyart is in m/s; convert to knots.
        if product == "velocity":
            data = data * 1.94384

        return Level2RadarScan(
            site=self._station,
            product=meta["label"].split("(")[-1].rstrip(")"),
            scan_time=scan_time,
            data=data,
            lats=gate_lat.astype(np.float32),
            lons=gate_lon.astype(np.float32),
            vmin=meta["vmin"],
            vmax=meta["vmax"],
            units=meta["units"],
            colormap=meta["colormap"],
            tilt_deg=tilt_deg,
            available_tilts=available_tilts,
            available_products=available_products,
            pyart_field=product,
        )

    # ── Buffer maintenance ────────────────────────────────────────────────────

    def _maintain_buffer(self, current_scan_time: datetime) -> None:
        """Pre-fetch BUFFER_BEFORE + BUFFER_AFTER scans around current_scan_time."""
        with self._index_lock:
            idx = self._index
        if not idx or current_scan_time not in idx:
            return
        pos = idx.index(current_scan_time)
        lo = max(0, pos - BUFFER_BEFORE)
        hi = min(len(idx) - 1, pos + BUFFER_AFTER)
        for i in range(lo, hi + 1):
            t = idx[i]
            if self._cache.get(t) is None and t not in self._pending_fetches:
                self._ensure_fetched(t)

        # Evict scans far outside the buffer to keep memory manageable.
        evict_lo = max(0, pos - BUFFER_BEFORE - 4)
        evict_hi = min(len(idx) - 1, pos + BUFFER_AFTER + 4)
        for t in list(self._cache.keys()):
            i = idx.index(t) if t in idx else -1
            if i == -1 or i < evict_lo or i > evict_hi:
                del self._cache[t]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_l2_filename_time(fname: str, station: str) -> Optional[datetime]:
    """
    Extract UTC datetime from a Level-2 filename.
    Expected pattern: KXXX20230515_201500_V06
    """
    try:
        # Strip station prefix (4 chars).
        rest = fname[4:]
        # rest = "20230515_201500_V06"
        date_part, rest2 = rest.split("_", 1)
        time_part = rest2.split("_")[0]
        dt_str = f"{date_part}{time_part}"
        return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None
