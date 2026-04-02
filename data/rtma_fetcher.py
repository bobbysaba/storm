# data/rtma_fetcher.py
# Polls the NSSL-hosted RTMA Rapid Update PNG catalog every 15 minutes.
#
# Server URL base:
#   https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/storm/rtma/
#
# Files served:
#   metadata.json   — valid_time, bounds [W,S,E,N], variables list, px dimensions
#   TMP.png         — 2m temperature  (K, RdYlBu_r, 253–313)
#   DPT.png         — 2m dewpoint     (K, YlGn,     243–303)
#   WIND.png        — 10m wind speed  (m/s, YlOrRd, 0–25)
#   GUST.png        — wind gust       (m/s, YlOrRd, 0–35)
#
# The PNG images are RGBA rasters aligned to the mbtiles domain bounds.
# This fetcher stores each PNG as both raw bytes (for map injection) and a
# PIL Image (for cursor value lookup via reverse-colormap sampling).
#
# SSL note: NSSL THREDDS uses a cert that Python's default context rejects.
# User-Agent note: nginx blocks Python's default UA; must set a browser UA.

import base64
import io
import logging
import ssl
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
import json

import numpy as np
from PIL import Image

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# ── Network config ────────────────────────────────────────────────────────────

RTMA_BASE = (
    "https://data.nssl.noaa.gov/thredds/fileServer/"
    "FOFS/Mobile-Mesonet/storm/rtma"
)
POLL_MS          = 15 * 60 * 1000   # 15 min
REQUEST_TIMEOUT  = 30
_HEADERS         = {"User-Agent": "Mozilla/5.0 STORM/1.0"}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ── Colormap metadata (must mirror server-side RENDER_PARAMS) ─────────────────
# vmin/vmax are in native GRIB units. unit_fn converts to display units.

def _k_to_f(k):  return (k - 273.15) * 9 / 5 + 32
def _ms_to_mph(v): return v * 2.23694

VAR_META = {
    "TMP":  {"cmap": "RdYlBu_r", "vmin": 253, "vmax": 313, "label": "2m Temp",     "unit": "°F", "convert": _k_to_f},
    "DPT":  {"cmap": "YlGn",     "vmin": 243, "vmax": 303, "label": "2m Dewpoint",  "unit": "°F", "convert": _k_to_f},
    "WIND": {"cmap": "YlOrRd",   "vmin": 0,   "vmax": 25,  "label": "Wind Speed",   "unit": "mph","convert": _ms_to_mph},
    "GUST": {"cmap": "YlOrRd",   "vmin": 0,   "vmax": 35,  "label": "Wind Gust",    "unit": "mph","convert": _ms_to_mph},
}

# Pre-compute reverse-colormap LUTs once at import time (256 RGB entries each).
# Used by RtmaFrame.value_at() to translate pixel colour → scalar.
_CLUT: dict[str, np.ndarray] = {}

def _build_luts():
    try:
        import matplotlib.pyplot as plt
        for alias, meta in VAR_META.items():
            cmap = plt.get_cmap(meta["cmap"])
            # 256 × 3 float32 RGB, values 0–1
            lut = cmap(np.linspace(0, 1, 256))[:, :3].astype(np.float32)
            _CLUT[alias] = lut
    except Exception as exc:
        log.warning("rtma_fetcher: could not build colourmap LUTs: %s", exc)

_build_luts()


# ── Data container ─────────────────────────────────────────────────────────────

@dataclass
class RtmaFrame:
    """
    One complete RTMA update — all variables from a single valid_time.

    Attributes
    ----------
    valid_time   UTC datetime of the analysis cycle.
    fetched_at   UTC datetime when this client downloaded the data.
    bounds       [min_lon, min_lat, max_lon, max_lat] matching mbtiles domain.
    variables    Ordered list of alias strings present in this frame.
    b64          alias → base64-encoded PNG string (for MapLibre injection).
    images       alias → PIL.Image (RGBA) — used for cursor value lookup.
    width_px     Raster width in pixels.
    height_px    Raster height in pixels.
    """
    valid_time: datetime
    fetched_at: datetime
    bounds:     list           # [W, S, E, N]
    variables:  list[str]
    b64:        dict[str, str]       = field(default_factory=dict)
    images:     dict[str, object]    = field(default_factory=dict)   # PIL.Image
    width_px:   int                  = 1024
    height_px:  int                  = 672

    @property
    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.valid_time).total_seconds() / 60

    @property
    def valid_str(self) -> str:
        return self.valid_time.strftime("%H:%MZ")

    def value_at(self, alias: str, lat: float, lon: float) -> str | None:
        """
        Return a formatted string like "72.4°F" for the given lat/lon, or None
        if the point is outside the domain or has no data (transparent pixel).
        Uses reverse-colormap lookup on the stored PIL image.
        """
        img = self.images.get(alias)
        lut = _CLUT.get(alias)
        meta = VAR_META.get(alias)
        if img is None or lut is None or meta is None:
            return None

        min_lon, min_lat, max_lon, max_lat = self.bounds
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            return None

        px_x = int((lon - min_lon) / (max_lon - min_lon) * (self.width_px - 1))
        px_y = int((max_lat - lat) / (max_lat - min_lat) * (self.height_px - 1))
        px_x = max(0, min(self.width_px - 1,  px_x))
        px_y = max(0, min(self.height_px - 1, px_y))

        try:
            r, g, b, a = img.getpixel((px_x, px_y))
        except Exception:
            return None

        if a < 10:
            return None   # transparent — no data

        # reverse-map RGB → normalised value [0,1] via nearest-neighbour in LUT
        pixel_rgb = np.array([r, g, b], dtype=np.float32) / 255.0
        dists = np.sum((lut - pixel_rgb) ** 2, axis=1)
        norm_val = np.argmin(dists) / 255.0
        raw_val  = meta["vmin"] + norm_val * (meta["vmax"] - meta["vmin"])
        disp_val = meta["convert"](raw_val)

        unit = meta["unit"]
        if unit == "°F":
            return f"{disp_val:.1f}{unit}"
        else:
            return f"{disp_val:.0f} {unit}"


# ── Fetcher ────────────────────────────────────────────────────────────────────

class RtmaFetcher(QObject):
    """
    Polls the NSSL RTMA catalog every 15 minutes.

    Signals
    -------
    data_updated(RtmaFrame)   New frame available (emitted on Qt main thread).
    status_updated(str)       Short human-readable status string.
    fetch_error(str)          Non-fatal error message.
    """

    data_updated   = pyqtSignal(object)   # RtmaFrame
    status_updated = pyqtSignal(str)
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_valid_time: str | None = None
        self._inflight = False
        self._lock     = threading.Lock()
        self._timer    = QTimer(self)
        self._timer.timeout.connect(self._poll)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        self._timer.start(POLL_MS)
        QTimer.singleShot(500, self._poll)
        log.info("RtmaFetcher: started (poll every 15 min)")

    def stop(self):
        self._timer.stop()

    def fetch_now(self):
        QTimer.singleShot(0, self._poll)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _poll(self):
        with self._lock:
            if self._inflight:
                return
            self._inflight = True
        threading.Thread(target=self._bg_fetch, daemon=True).start()

    def _bg_fetch(self):
        try:
            self.status_updated.emit("RTMA: fetching…")
            frame = _fetch_frame(self._last_valid_time)
            if frame is None:
                self.status_updated.emit(
                    f"RTMA: up to date ({self._last_valid_time})"
                )
            else:
                self._last_valid_time = frame.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                age = int(frame.age_minutes)
                self.status_updated.emit(
                    f"RTMA: {frame.valid_str}  ({age} min ago)"
                )
                self.data_updated.emit(frame)
                log.info(
                    "RtmaFetcher: new frame %s  vars=%s",
                    frame.valid_str, frame.variables,
                )
        except (HTTPError, URLError, OSError) as exc:
            msg = f"RTMA fetch failed: {exc}"
            log.warning(msg)
            self.status_updated.emit("RTMA: fetch failed")
            self.fetch_error.emit(msg)
        except Exception as exc:
            msg = f"RTMA error: {exc}"
            log.exception(msg)
            self.status_updated.emit("RTMA: error")
            self.fetch_error.emit(msg)
        finally:
            with self._lock:
                self._inflight = False


# ── Worker (runs in background thread) ────────────────────────────────────────

def _fetch(url: str) -> bytes:
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
        return resp.read()


def _fetch_frame(last_valid_time: str | None) -> RtmaFrame | None:
    """
    Fetch metadata.json and, if the valid_time is new, download PNGs.
    Returns None if valid_time is unchanged (no new data).
    """
    meta_bytes = _fetch(f"{RTMA_BASE}/metadata.json")
    meta = json.loads(meta_bytes.decode())

    valid_time_str = meta["valid_time"]
    if valid_time_str == last_valid_time:
        return None

    valid_time = datetime.fromisoformat(valid_time_str.replace("Z", "+00:00"))
    bounds     = meta["bounds"]        # [W, S, E, N]
    variables  = meta["variables"]
    width_px   = meta.get("width_px",  1024)
    height_px  = meta.get("height_px", 672)

    b64_map    = {}
    image_map  = {}

    for alias in variables:
        if alias not in VAR_META:
            continue
        try:
            png_bytes = _fetch(f"{RTMA_BASE}/{alias}.png")
            b64_map[alias]   = base64.b64encode(png_bytes).decode("ascii")
            image_map[alias] = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception as exc:
            log.warning("RtmaFetcher: could not fetch %s.png: %s", alias, exc)

    return RtmaFrame(
        valid_time = valid_time,
        fetched_at = datetime.now(timezone.utc),
        bounds     = bounds,
        variables  = [v for v in variables if v in b64_map],
        b64        = b64_map,
        images     = image_map,
        width_px   = width_px,
        height_px  = height_px,
    )
