# data/mesoanalysis_fetcher.py
# SPC Mesoanalysis (SfcOA) overlay fetcher.
#
# Downloads gif parameter images from SPC's mesoanalysis page for the selected
# sector, crops the baked-in legend, warps the Lambert-Conformal gif to a
# rectangular lat/lon PNG so MapLibre's image source can place it without
# quad distortion, caches the result in an LRU, and emits a MesoFrame.
#
# URL patterns (empirical; sector ids come from SPC's carto.js initmap calls):
#   observed:  /exper/mesoanalysis/s{sector}/{param}/{param}.gif           (now)
#              /exper/mesoanalysis/s{sector}/{param}/{param}_{YYMMDDHH}.gif (archive)
#   forecast:  /exper/mesoanalysis/fcst/s{sector}/{param}_{HH}_trans.gif    (valid-hour UTC)
#
# Projection port: SPC uses a custom LCC implementation in carto.js with
# earth radius 6371 km, standard parallels (slat1, slat2), reference longitude
# (slon), and a pixel scale ("zoom"/xscle).  The math here is a direct port of
# initmap/lalo_xy/xy_lalo/pix_xy so the computed pixel↔lat/lon mapping matches
# SPC's own to within rounding.

from __future__ import annotations

import base64
import io
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

SPC_BASE = "https://www.spc.noaa.gov/exper/mesoanalysis"
REQUEST_TIMEOUT = 20
CACHE_SIZE = 18          # LRU entries keyed by (sector, param, hour_offset)
REFRESH_INTERVAL_MS = 15 * 60 * 1000
FORECAST_STEP_HOURS = 2  # SPC forecast cadence
LEGEND_CROP_PX = 110     # bottom strip with the color scale (1000×750 image)


# ── Sector table ─────────────────────────────────────────────────────────────
# Ported from carto.js setMap(): each entry is (clat, clon, slat1, slat2, slon,
# zoom, width, height, meshsize).  The sector id matches the s{n} used in the
# SPC URL.  s19 is full CONUS (wider std parallels, smaller zoom); the rest are
# regional sectors that tile the country with overlap.
_SECTOR_PARAMS: dict[int, tuple] = {
    11: (41.90, -116.40, 35, 50, -98.5, 20.0, 1000, 750, 40),
    12: (33.07, -114.67, 35, 50, -98.5, 20.0, 1000, 750, 40),
    13: (42.38,  -99.85, 35, 50, -98.5, 21.9, 1000, 750, 40),
    14: (35.70,  -99.86, 35, 50, -98.0, 22.5, 1000, 750, 40),
    15: (29.50,  -99.70, 35, 50, -97.8, 19.1, 1000, 750, 40),
    16: (42.50,  -80.90, 35, 50, -96.5, 22.5, 1000, 750, 40),
    17: (35.20,  -85.10, 35, 50, -97.0, 23.1, 1000, 750, 40),
    18: (28.45,  -89.20, 35, 50, -97.0, 19.2, 1000, 750, 40),
    19: (32.60, -103.20, 35, 45, -98.0,  8.3, 1000, 750, 40),  # CONUS
    20: (37.20,  -94.60, 35, 50, -97.5, 21.5, 1000, 750, 40),
    21: (42.80,  -88.80, 35, 50, -97.0, 25.0, 1000, 750, 40),
    22: (38.35, -112.30, 35, 50, -99.0, 23.4, 1000, 750, 40),
}

# Human-readable labels, in the order they should appear in the dropdown.
SECTOR_LABELS: list[tuple[int, str]] = [
    (14, "S Plains"),
    (13, "N Plains"),
    (15, "S Texas / Gulf"),
    (18, "Gulf Coast"),
    (17, "SE / TN Valley"),
    (16, "Great Lakes / NE"),
    (21, "Upper Midwest"),
    (20, "Central"),
    (12, "Desert SW"),
    (11, "Pacific NW"),
    (22, "Interior West"),
    (19, "CONUS"),
]

REGIONAL_SECTORS = [sid for sid, _ in SECTOR_LABELS if sid != 19]


# ── Parameter table ──────────────────────────────────────────────────────────
# (slug, display_name, category, forecast_capable)
PARAMS: list[tuple[str, str, str, bool]] = [
    # Thermo
    ("sbcp", "SBCAPE",                "Thermo",    True),
    ("mlcp", "MLCAPE",                "Thermo",    True),
    ("mucp", "MUCAPE",                "Thermo",    False),
    ("dcape","DCAPE",                 "Thermo",    True),
    ("laps", "700–500 mb LR",         "Thermo",    True),
    ("lllr", "0–3 km LR",             "Thermo",    True),
    ("lclh", "LCL Height",            "Thermo",    False),
    ("lfch", "LFC Height",            "Thermo",    False),
    # Kinematic
    ("eshr", "Eff. Bulk Shear",       "Kinematic", True),
    ("shr1", "0–1 km Shear",          "Kinematic", False),
    ("shr3", "0–3 km Shear",          "Kinematic", False),
    ("shr6", "0–6 km Shear",          "Kinematic", False),
    ("effh", "Eff. SRH",              "Kinematic", True),
    ("srh5", "0–500 m SRH",           "Kinematic", False),
    ("srh1", "0–1 km SRH",            "Kinematic", True),
    ("srh3", "0–3 km SRH",            "Kinematic", False),
    # Composite
    ("scp",  "Supercell Composite",   "Composite", True),
    ("stor", "Sig Tor (fixed)",       "Composite", False),
    ("stpc", "Sig Tor (effective)",   "Composite", True),
    ("stpc5","Sig Tor (0–500 m SRH)", "Composite", False),
    ("ehi1", "EHI 0–1 km",            "Composite", False),
    ("ehi3", "EHI 0–3 km",            "Composite", False),
    ("crit", "Critical Angle",        "Composite", False),
    # Multi-parameter
    ("tdlr",      "Sfc Dwpt / Mid LR",        "Multi", False),
    ("mlcp_eshr", "MLCAPE / Eff Shear",       "Multi", False),
]

PARAM_CATEGORIES = ["Thermo", "Kinematic", "Composite", "Multi"]


def param_info(slug: str) -> tuple[str, str, bool] | None:
    """Return (display_name, category, forecast_capable) or None if unknown."""
    for sl, name, cat, fc in PARAMS:
        if sl == slug:
            return name, cat, fc
    return None


# ── LCC projection port (from carto.js) ──────────────────────────────────────
# The original JS does: initmap → {lalo_xy,xy_lalo} + pix_xy.  Here we give each
# sector its own SpcLcc instance computed from the initmap arguments.

_EARTH_RADIUS_KM = 6371.0
_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi


class SpcLcc:
    """Port of SPC's custom Lambert Conformal routines for a single sector."""

    __slots__ = (
        "slat1", "slat2", "slon", "clat", "clon", "zoom", "width", "height",
        "mesh", "coneconst", "psi", "rho1", "xxl", "yyl",
    )

    def __init__(self, params: tuple):
        clat, clon, slat1, slat2, slon, zoom, wid, hgt, mesh = params
        self.slat1 = slat1
        self.slat2 = slat2
        self.slon  = slon
        self.clat  = clat
        self.clon  = clon
        self.zoom  = zoom
        self.width = wid
        self.height = hgt
        self.mesh  = mesh

        t1 = math.log(math.cos(_D2R * slat1) / math.cos(_D2R * slat2))
        t2 = math.log(
            math.tan(_D2R * (45.0 - slat1 / 2.0))
            / math.tan(_D2R * (45.0 - slat2 / 2.0))
        )
        self.coneconst = 1.0 if t2 == 0 else (t1 / t2)

        term1 = _EARTH_RADIUS_KM * math.cos(_D2R * slat1)
        term2 = math.tan(_D2R * (45.0 - slat1 / 2.0)) ** self.coneconst
        self.psi  = term1 / (self.coneconst * term2)
        self.rho1 = self.psi * term2

        self.xxl, self.yyl = self.lalo_xy(clat, clon)

    # lat/lon → LCC x,y
    def lalo_xy(self, lat: float, lon: float) -> tuple[float, float]:
        theta = _D2R * (lon - self.slon) * self.coneconst
        rho   = self.psi * (math.tan(_D2R * (45.0 - lat / 2.0)) ** self.coneconst)
        x = (1.0 / self.mesh) * rho * math.sin(theta)
        y = (1.0 / self.mesh) * (self.rho1 - rho * math.cos(theta))
        return x, y

    # LCC x,y → lat/lon
    def xy_lalo(self, x: float, y: float) -> tuple[float, float]:
        n1 = x / (1.0 / self.mesh)
        d1 = self.rho1 - y / (1.0 / self.mesh)
        theta = math.atan2(n1, d1) if d1 != 0 or n1 != 0 else 0.0
        lon = self.slon + (1.0 / self.coneconst) * _R2D * theta

        if theta != 0:
            nume = abs(x) ** (1.0 / self.coneconst)
            denom = abs((1.0 / self.mesh) * self.psi * math.sin(theta)) ** (1.0 / self.coneconst)
            lat = 90.0 - 2.0 * _R2D * math.atan(nume / denom)
        else:
            maxv = abs(y - (1.0 / self.mesh) * self.rho1)
            t3 = maxv ** (1.0 / self.coneconst)
            t4 = (self.psi * (1.0 / self.mesh)) ** (1.0 / self.coneconst)
            lat = 90.0 - 2.0 * _R2D * math.atan(t3 / t4)
        return lat, lon

    # pixel → LCC x,y  (pix_xy from carto.js)
    def pix_xy(self, px: float, py: float) -> tuple[float, float]:
        x = ((px - self.width / 2.0) / self.zoom) + self.xxl
        y = ((-py + self.height / 2.0) / self.zoom) + self.yyl
        return x, y

    # LCC x,y → pixel  (inverse of pix_xy)
    def xy_pix(self, x: float, y: float) -> tuple[float, float]:
        px = (x - self.xxl) * self.zoom + self.width / 2.0
        py = (self.yyl - y) * self.zoom + self.height / 2.0
        return px, py

    def pix_lalo(self, px: float, py: float) -> tuple[float, float]:
        x, y = self.pix_xy(px, py)
        return self.xy_lalo(x, y)

    def lalo_pix(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = self.lalo_xy(lat, lon)
        return self.xy_pix(x, y)

    def corner_bbox(self, crop_bottom: int = 0) -> tuple[float, float, float, float]:
        """Approximate axis-aligned lat/lon bbox (west, south, east, north) of
        the image pixels (0..w, 0..h-crop).  Walks the image border to capture
        the LCC curvature."""
        h_eff = self.height - crop_bottom
        lats: list[float] = []
        lons: list[float] = []
        steps = 64
        for i in range(steps + 1):
            t = i / steps
            # top
            la, lo = self.pix_lalo(t * self.width, 0)
            lats.append(la); lons.append(lo)
            # bottom (above cropped legend)
            la, lo = self.pix_lalo(t * self.width, h_eff)
            lats.append(la); lons.append(lo)
            # left
            la, lo = self.pix_lalo(0, t * h_eff)
            lats.append(la); lons.append(lo)
            # right
            la, lo = self.pix_lalo(self.width, t * h_eff)
            lats.append(la); lons.append(lo)
        return min(lons), min(lats), max(lons), max(lats)


_LCC: dict[int, SpcLcc] = {sid: SpcLcc(p) for sid, p in _SECTOR_PARAMS.items()}


def sector_projection(sector: int) -> SpcLcc:
    return _LCC[sector]


def pick_nearest_sector(lat: float, lon: float) -> int:
    """Return the regional sector whose center is closest to (lat, lon).
    Falls back to s19 (CONUS) if the nearest regional center is absurdly far
    (viewport outside the mesoanalysis coverage)."""
    best_id = 19
    best_d  = float("inf")
    for sid in REGIONAL_SECTORS:
        clat, clon, *_ = _SECTOR_PARAMS[sid]
        d = _haversine_km(lat, lon, clat, clon)
        if d < best_d:
            best_d = d
            best_id = sid
    if best_d > 2500.0:
        return 19
    return best_id


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = lat1 * _D2R
    phi2 = lat2 * _D2R
    dphi = (lat2 - lat1) * _D2R
    dlam = (lon2 - lon1) * _D2R
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ── Frame / cache ────────────────────────────────────────────────────────────

@dataclass
class MesoFrame:
    sector: int
    param:  str
    hour_offset: int            # -2, -1, 0 (observed) or +2, +4, +6 (forecast)
    b64:    str                 # base64 PNG data (already warped to lat/lon)
    west:   float
    south:  float
    east:   float
    north:  float
    fetched_at: datetime

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.sector, self.param, self.hour_offset)


class _LRU:
    def __init__(self, cap: int):
        self.cap = cap
        self.d: "OrderedDict[tuple, MesoFrame]" = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key) -> MesoFrame | None:
        with self.lock:
            v = self.d.get(key)
            if v is not None:
                self.d.move_to_end(key)
            return v

    def put(self, key, frame: MesoFrame):
        with self.lock:
            self.d[key] = frame
            self.d.move_to_end(key)
            while len(self.d) > self.cap:
                self.d.popitem(last=False)

    def discard_sector(self, sector: int):
        with self.lock:
            for k in list(self.d.keys()):
                if k[0] == sector:
                    del self.d[k]


# ── Fetcher ──────────────────────────────────────────────────────────────────

def build_url(sector: int, param: str, hour_offset: int,
              now_utc: datetime | None = None) -> str:
    """Return the SPC URL for a given (sector, param, hour_offset)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if hour_offset < 0:
        # Observed archive: YYMMDDHH of the target UTC hour
        t = now_utc + timedelta(hours=hour_offset)
        stamp = t.strftime("%y%m%d%H")
        return f"{SPC_BASE}/s{sector}/{param}/{param}_{stamp}.gif"
    if hour_offset == 0:
        return f"{SPC_BASE}/s{sector}/{param}/{param}.gif"
    # Forecast: valid-time UTC 2-digit hour
    t = now_utc + timedelta(hours=hour_offset)
    hh = t.strftime("%H")
    return f"{SPC_BASE}/fcst/s{sector}/{param}_{hh}_trans.gif"


def _clean_overlay_rgba(arr: np.ndarray) -> np.ndarray:
    """
    Suppress likely baked-in grayscale basemap pixels while preserving most
    colored fill/contour content. This is intentionally conservative so we can
    experiment without wiping out the actual meteorological shading.
    """
    if arr.size == 0:
        return arr

    out = arr.copy()
    rgb = out[..., :3].astype(np.int16)
    alpha = out[..., 3].astype(np.uint8)

    maxc = rgb.max(axis=2)
    minc = rgb.min(axis=2)
    spread = maxc - minc
    mean = rgb.mean(axis=2)

    opaque = alpha > 0
    low_chroma = spread <= 20
    midtone = (mean >= 55) & (mean <= 235)
    near_white = mean >= 244
    very_dark = maxc <= 40

    # Fully remove the light/neutral paper background and faint gray linework.
    drop = opaque & low_chroma & (midtone | near_white) & ~very_dark
    out[drop, 3] = 0

    # Strongly fade dark neutral linework instead of deleting it outright so
    # any real black contours still have a chance to remain visible.
    dark_neutral = opaque & low_chroma & (maxc > 40) & (maxc <= 95)
    faded_alpha = (alpha[dark_neutral].astype(np.float32) * 0.18).astype(np.uint8)
    out[dark_neutral, 3] = faded_alpha

    return out


def _suppress_diagonal_hatch(arr: np.ndarray, passes: int = 2) -> np.ndarray:
    """
    Some SPC products bake a thin diagonal hatch into shaded areas. Collapse
    pixels that look like one-pixel diagonal stripes back toward the surrounding
    fill color while leaving broader contours mostly untouched.
    """
    if arr.size == 0:
        return arr

    out = arr.copy()
    for _ in range(max(1, passes)):
        rgb = out[..., :3].astype(np.int16)
        alpha = out[..., 3].astype(np.uint8)
        inner = rgb[1:-1, 1:-1]
        inner_a = alpha[1:-1, 1:-1]

        for n1, n2, a1, a2 in (
            (rgb[:-2, 2:], rgb[2:, :-2], alpha[:-2, 2:], alpha[2:, :-2]),
            (rgb[:-2, :-2], rgb[2:, 2:], alpha[:-2, :-2], alpha[2:, 2:]),
        ):
            avg = (n1 + n2) // 2
            neigh_similar = np.max(np.abs(n1 - n2), axis=2) <= 20
            different = np.sum(np.abs(inner - avg), axis=2) >= 50
            brighter = inner.mean(axis=2) >= (avg.mean(axis=2) + 10)
            opaque = (inner_a > 0) & (a1 > 0) & (a2 > 0)

            replace = neigh_similar & different & brighter & opaque
            inner[replace] = avg[replace]

        out[1:-1, 1:-1, :3] = np.clip(inner, 0, 255).astype(np.uint8)
    return out


class MesoanalysisFetcher(QObject):
    """
    Background fetcher for SPC mesoanalysis (SfcOA) overlays.

    Public API:
        start() / stop()
        set_active(sector, param, hour_offset)   — triggers a fetch
        request_refresh()                        — re-fetch current active
        clear_active()                           — stop refreshing and clear

    Signals:
        frame_ready(object)     — MesoFrame ready for display
        fetch_error(str)        — user-visible error
        fetch_started()         — worker started
        fetch_finished()        — worker finished (success or error)
    """

    frame_ready    = pyqtSignal(object)
    fetch_error    = pyqtSignal(str)
    fetch_started  = pyqtSignal()
    fetch_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = _LRU(CACHE_SIZE)
        self._active: tuple[int, str, int] | None = None
        self._inflight: set[tuple[int, str, int]] = set()
        self._lock = threading.Lock()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.request_refresh)

    # ── Public ──────────────────────────────────────────────────────────────

    def start(self):
        self._refresh_timer.start()

    def stop(self):
        self._refresh_timer.stop()

    def set_active(self, sector: int, param: str, hour_offset: int):
        key = (sector, param, hour_offset)
        with self._lock:
            self._active = key
        cached = self._cache.get(key)
        if cached is not None:
            self.frame_ready.emit(cached)
            return
        self._spawn(key)

    def clear_active(self):
        with self._lock:
            self._active = None

    def request_refresh(self):
        with self._lock:
            key = self._active
        if key is None:
            return
        # Bypass cache for observed-now; forecasts and archives don't change.
        if key[2] == 0:
            self._cache.d.pop(key, None)
        self._spawn(key)

    # ── Worker management ──────────────────────────────────────────────────

    def _spawn(self, key: tuple[int, str, int]):
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        self.fetch_started.emit()
        threading.Thread(target=self._worker, args=(key,), daemon=True).start()

    def _worker(self, key: tuple[int, str, int]):
        sector, param, hour = key
        try:
            url = build_url(sector, param, hour)
            log.debug("MesoanalysisFetcher: GET %s", url)
            data = self._http_get(url)
            frame = self._decode_and_warp(sector, param, hour, data)
            self._cache.put(key, frame)
            with self._lock:
                still_active = (self._active == key)
            if still_active:
                self.frame_ready.emit(frame)
        except Exception as exc:
            log.warning("MesoanalysisFetcher: %s %s/%s h=%+d → %s",
                        type(exc).__name__, f"s{sector}", param, hour, exc)
            self.fetch_error.emit(str(exc))
        finally:
            with self._lock:
                self._inflight.discard(key)
            self.fetch_finished.emit()

    def _http_get(self, url: str) -> bytes:
        req = Request(url, headers={"User-Agent": "STORM/1.0"})
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                ct = resp.headers.get("Content-Type", "")
                if "image" not in ct:
                    raise RuntimeError(f"unexpected Content-Type: {ct}")
                return resp.read()
        except HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}") from e
        except URLError as e:
            raise RuntimeError(str(e.reason)) from e

    # ── Decode + warp ──────────────────────────────────────────────────────

    def _decode_and_warp(self, sector: int, param: str, hour: int,
                         data: bytes) -> MesoFrame:
        """
        Decode an SPC gif, crop out the baked-in legend strip at the bottom,
        and warp the remaining LCC pixels onto a rectangular lat/lon grid so
        MapLibre's image source can place it without quad distortion.
        """
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        if im.size != (1000, 750):
            log.warning("MesoanalysisFetcher: unexpected size %s", im.size)

        src_w, src_h = im.size
        src_h_eff = max(1, src_h - LEGEND_CROP_PX)
        cropped = im.crop((0, 0, src_w, src_h_eff))
        src_arr = np.asarray(cropped, dtype=np.uint8)     # (H, W, 4)
        src_arr = _suppress_diagonal_hatch(src_arr)
        src_arr = _clean_overlay_rgba(src_arr)

        lcc = _LCC[sector]
        west, south, east, north = lcc.corner_bbox(crop_bottom=LEGEND_CROP_PX)
        # Slight inset so the warped edges are interior samples.
        west  += 0.02
        east  -= 0.02
        south += 0.02
        north -= 0.02

        out_w = src_w
        out_h = src_h_eff
        # Build inverse map: for each output pixel (ox,oy), compute source
        # pixel (px,py) via the LCC forward transform.
        lons = np.linspace(west,  east,  out_w, dtype=np.float64)
        lats = np.linspace(north, south, out_h, dtype=np.float64)   # top → bottom
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        # Forward project (lat,lon) → LCC x,y
        theta = (lon_grid - lcc.slon) * _D2R * lcc.coneconst
        tan_arg = np.tan(_D2R * (45.0 - lat_grid / 2.0))
        tan_arg = np.clip(tan_arg, 1e-9, None)
        rho   = lcc.psi * (tan_arg ** lcc.coneconst)
        x = (1.0 / lcc.mesh) * rho * np.sin(theta)
        y = (1.0 / lcc.mesh) * (lcc.rho1 - rho * np.cos(theta))
        # LCC x,y → source pixel
        px = (x - lcc.xxl) * lcc.zoom + lcc.width  / 2.0
        py = (lcc.yyl - y) * lcc.zoom + lcc.height / 2.0

        # Nearest-neighbor sample (palettized gifs — bilinear would smudge
        # color boundaries).  Mask out-of-bounds → transparent.
        ix = np.rint(px).astype(np.int32)
        iy = np.rint(py).astype(np.int32)
        valid = (
            (ix >= 0) & (ix < src_w) &
            (iy >= 0) & (iy < src_h_eff)
        )
        ix = np.clip(ix, 0, src_w - 1)
        iy = np.clip(iy, 0, src_h_eff - 1)

        out_arr = src_arr[iy, ix]
        out_arr[~valid] = (0, 0, 0, 0)
        out_arr = _suppress_diagonal_hatch(out_arr)
        out_arr = _clean_overlay_rgba(out_arr)

        out_im = Image.fromarray(out_arr, mode="RGBA")
        buf = io.BytesIO()
        out_im.save(buf, format="PNG", optimize=False)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        return MesoFrame(
            sector=sector,
            param=param,
            hour_offset=hour,
            b64=b64,
            west=west,
            south=south,
            east=east,
            north=north,
            fetched_at=datetime.now(timezone.utc),
        )
