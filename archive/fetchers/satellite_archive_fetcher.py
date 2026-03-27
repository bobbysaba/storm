# archive/fetchers/satellite_archive_fetcher.py
# ArchiveSatelliteFetcher — retrieves historical GOES-16 CONUS imagery from
# the IEM WMS service with TIME dimension support.
#
# IEM serves archived GOES-16 data through a WMS endpoint.  We first fetch
# GetCapabilities to discover available time steps for the session date, then
# fetch individual frames on demand as the archive time advances.
#
# WMS base:  https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ch02.cgi
# Band options: ch02 (visible), ch13 (IR clean window)

import base64
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

# IEM GOES WMS endpoints keyed by band label.
_WMS_ENDPOINTS = {
    "Visible (Ch 02)": "https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ch02.cgi",
    "IR (Ch 13)":      "https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ch13.cgi",
}
DEFAULT_BAND = "Visible (Ch 02)"

# WMS request parameters for a 1024×512 CONUS PNG.
_WMS_PARAMS = {
    "SERVICE":     "WMS",
    "VERSION":     "1.3.0",
    "REQUEST":     "GetMap",
    "LAYERS":      "",     # filled per endpoint
    "STYLES":      "",
    "CRS":         "EPSG:4326",
    "BBOX":        "24,-126,50,-66",  # south,west,north,east for 1.3.0
    "WIDTH":       "1024",
    "HEIGHT":      "512",
    "FORMAT":      "image/png",
    "TRANSPARENT": "TRUE",
}

# CONUS bounding box [west, south, east, north].
CONUS_BBOX = [-126.0, 24.0, -66.0, 50.0]

# How many frames to keep in the decoded cache.
_MAX_CACHE = 20


class SatFrame:
    """A single satellite frame (base64-encoded PNG + valid time + bbox)."""
    __slots__ = ("timestamp", "b64", "bbox")

    def __init__(self, timestamp: datetime, b64: str, bbox: list):
        self.timestamp = timestamp
        self.b64 = b64
        self.bbox = bbox   # [west, south, east, north]


class ArchiveSatelliteFetcher(QObject):
    """
    Provides historical GOES-16 CONUS imagery for archive playback.

    Workflow
    --------
    1. Call ``load_capabilities()`` once at session start to fetch the list of
       available times from IEM WMS GetCapabilities.
    2. Connect ``on_time_changed`` to the TimeController ``time_changed`` signal.
    3. The fetcher emits ``frame_ready(SatFrame)`` whenever a frame is available
       for the current archive time.

    Signals
    -------
    frame_ready(SatFrame)
        Emitted when the frame for the current archive time has been fetched.
    capabilities_loaded(list[str])
        List of available ISO-format time strings for the session date.
    loading_changed(bool)
    error(str)
    """

    frame_ready          = pyqtSignal(object)   # SatFrame
    capabilities_loaded  = pyqtSignal(list)     # list[str] ISO times
    loading_changed      = pyqtSignal(bool)
    error                = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date   = session_date
        self._band   = DEFAULT_BAND
        self._times: list[datetime] = []      # available times from caps
        self._cache: dict[datetime, Optional[SatFrame]] = {}
        self._pending: set[datetime] = set()
        self._fetch_lock = threading.Lock()
        self._current_archive_time: Optional[datetime] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_band(self, band_label: str) -> None:
        """Switch the satellite band (clears cache)."""
        if band_label not in _WMS_ENDPOINTS:
            return
        self._band = band_label
        self._cache.clear()
        self._pending.clear()
        if self._current_archive_time:
            self.on_time_changed(self._current_archive_time)

    @staticmethod
    def band_options() -> list:
        return list(_WMS_ENDPOINTS.keys())

    def load_capabilities(self) -> None:
        """Fetch available times from WMS GetCapabilities (background)."""
        t = threading.Thread(target=self._fetch_capabilities, daemon=True)
        t.start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """Called by TimeController on every tick."""
        self._current_archive_time = archive_time
        nearest = self._nearest_time(archive_time)
        if nearest is None:
            return
        frame = self._cache.get(nearest)
        if frame is not None:
            self.frame_ready.emit(frame)
        else:
            self._ensure_fetched(nearest)

    # ── Capabilities ──────────────────────────────────────────────────────────

    def _fetch_capabilities(self) -> None:
        endpoint = _WMS_ENDPOINTS[self._band]
        url = f"{endpoint}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            times = _parse_wms_times(resp.text, self._date)
            self._times = sorted(times)
            log.info(
                "ArchiveSatelliteFetcher: %d frames available on %s",
                len(self._times), self._date.strftime("%Y-%m-%d"),
            )
            self.capabilities_loaded.emit(
                [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in self._times]
            )
            if self._current_archive_time and self._times:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: caps fetch failed: %s", exc)
            self.error.emit(f"Satellite caps failed: {exc}")

    # ── Frame fetching ────────────────────────────────────────────────────────

    def _nearest_time(self, t: datetime) -> Optional[datetime]:
        if not self._times:
            return None
        lo, hi = 0, len(self._times) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._times[mid] <= t:
                result = self._times[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _ensure_fetched(self, frame_time: datetime) -> None:
        with self._fetch_lock:
            if frame_time in self._pending or self._cache.get(frame_time) is not None:
                return
            self._pending.add(frame_time)
        self.loading_changed.emit(True)
        threading.Thread(
            target=self._fetch_frame, args=(frame_time,), daemon=True
        ).start()

    def _fetch_frame(self, frame_time: datetime) -> None:
        try:
            endpoint = _WMS_ENDPOINTS[self._band]
            layer = _layer_name(endpoint)
            params = dict(_WMS_PARAMS)
            params["LAYERS"] = layer
            params["TIME"] = frame_time.strftime("%Y-%m-%dT%H:%M:%SZ")

            resp = requests.get(endpoint, params=params, timeout=30)
            resp.raise_for_status()
            if "image" not in resp.headers.get("Content-Type", ""):
                raise ValueError("WMS returned non-image response")

            b64 = base64.b64encode(resp.content).decode()
            frame = SatFrame(
                timestamp=frame_time,
                b64=b64,
                bbox=CONUS_BBOX,
            )
            self._cache[frame_time] = frame

            # Evict oldest entries to cap memory usage.
            while len(self._cache) > _MAX_CACHE:
                oldest = min(self._cache.keys())
                del self._cache[oldest]

            if (
                self._current_archive_time is not None
                and self._nearest_time(self._current_archive_time) == frame_time
            ):
                self.frame_ready.emit(frame)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: frame fetch failed %s: %s", frame_time, exc)
            self.error.emit(f"Satellite frame failed: {exc}")
        finally:
            with self._fetch_lock:
                self._pending.discard(frame_time)
            with self._fetch_lock:
                if not self._pending:
                    self.loading_changed.emit(False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _layer_name(endpoint: str) -> str:
    """Derive the WMS layer name from the endpoint URL."""
    # e.g. .../conus_ch02.cgi → "conus_ch02"
    fname = endpoint.rsplit("/", 1)[-1]
    return fname.replace(".cgi", "")


def _parse_wms_times(xml_text: str, target_date: datetime) -> list:
    """Extract TIME dimension values from WMS GetCapabilities XML."""
    import xml.etree.ElementTree as ET
    times = []
    try:
        root = ET.fromstring(xml_text)
        # Look for <Extent name="time"> or <Dimension name="time">
        for tag in ("Extent", "Dimension"):
            for elem in root.iter(tag):
                if elem.get("name", "").lower() == "time":
                    content = (elem.text or "").strip()
                    # Comma-separated list of ISO timestamps.
                    for ts in content.split(","):
                        ts = ts.strip()
                        if not ts:
                            continue
                        try:
                            dt = datetime.fromisoformat(
                                ts.replace("Z", "+00:00")
                            ).astimezone(timezone.utc)
                            # Filter to the target date only.
                            if dt.date() == target_date.date():
                                times.append(dt)
                        except Exception:
                            continue
    except Exception as exc:
        log.warning("_parse_wms_times: %s", exc)
    return times
