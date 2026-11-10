import io
import logging
import math
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import numpy as np
import requests
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_S3_URL_TEMPLATE = "https://{bucket}.s3.amazonaws.com"
_GOES19_EAST_START = datetime(2025, 4, 4, 15, 0, tzinfo=timezone.utc)
_TIME_RE = re.compile(r"_s(?P<year>\d{4})(?P<jday>\d{3})(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})")

_MODE_CONFIG = {
    "conus": {
        "product": "ABI-L2-CMIPC",
        "label": "CONUS",
        "fixed_bbox": [-116.0, 28.0, -82.0, 49.0],
    },
    "meso1": {
        "product": "ABI-L2-CMIPM",
        "label": "MESO-1",
        "sector_token": "CMIPM1",
    },
    "meso2": {
        "product": "ABI-L2-CMIPM",
        "label": "MESO-2",
        "sector_token": "CMIPM2",
    },
}
_MAX_CACHE = 24
_REQUEST_TIMEOUT = 30
_FETCH_WORKERS = 2
# frames fetched ahead of the current one, in playback direction
_PREFETCH_AHEAD = 2
_OUTPUT_WIDTH = {"conus": 1500, "meso1": 1100, "meso2": 1100}


@dataclass(frozen=True)
class _FrameRef:
    timestamp: datetime
    key: str


class SatFrame:
    __slots__ = ("mode", "timestamp", "png", "bbox")

    def __init__(self, mode: str, timestamp: datetime, png: bytes, bbox: list[float]):
        self.mode = mode
        self.timestamp = timestamp
        self.png = png
        self.bbox = bbox

    @property
    def frame_id(self) -> str:
        return self.timestamp.strftime("%Y%m%dT%H%M%S")

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%MZ")

    @property
    def time_iso(self) -> str:
        return self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ArchiveSatelliteFetcher(QObject):
    """
    Historical GOES-East visible imagery backed by NOAA's public AWS archive.

    Supported modes:
      - conus  -> ABI-L2-CMIPC channel 02
      - meso1  -> ABI-L2-CMIPM sector M1 channel 02
      - meso2  -> ABI-L2-CMIPM sector M2 channel 02
    """

    frame_ready = pyqtSignal(object)          # SatFrame
    capabilities_loaded = pyqtSignal(list)    # list[str] for current mode
    loading_changed = pyqtSignal(bool)
    error = pyqtSignal(str)
    meso_sectors_updated = pyqtSignal(object)  # {1: bbox|None, 2: bbox|None}

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = _ensure_utc(session_date)
        self._bucket = _bucket_for_date(self._date)
        self._mode = ""   # no mode until user explicitly selects one
        self._indexes: dict[str, list[_FrameRef]] = {mode: [] for mode in _MODE_CONFIG}
        self._indexes_ready = False
        self._indexed_modes: set[str] = set()
        self._indexing_modes: set[str] = set()
        self._cache: dict[tuple[str, datetime], SatFrame] = {}
        self._pending: set[tuple[str, datetime]] = set()
        self._fetch_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=_FETCH_WORKERS, thread_name_prefix="sat-archive")
        self._current_archive_time: Optional[datetime] = None
        self._direction = 1   # +1 playing forward, -1 scrubbing backward
        self._meso_bboxes: dict[int, Optional[dict]] = {1: None, 2: None}


    def load_capabilities(self) -> None:
        # index all modes at startup so meso buttons are responsive immediately.
        for mode in _MODE_CONFIG:
            self._ensure_mode_index(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in _MODE_CONFIG:
            return
        self._mode = mode
        self._ensure_mode_index(mode)
        if mode in self._indexed_modes:
            self.capabilities_loaded.emit(self._mode_times_iso())
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def on_time_changed(self, archive_time: datetime) -> None:
        archive_time = _ensure_utc(archive_time)
        if self._current_archive_time is not None and archive_time != self._current_archive_time:
            self._direction = 1 if archive_time > self._current_archive_time else -1
        self._current_archive_time = archive_time
        refs = self._indexes.get(self._mode, [])
        pos = self._nearest_pos(self._mode, archive_time)
        if pos is None:
            return
        ref = refs[pos]
        frame = self._cache.get((self._mode, ref.timestamp))
        if frame is not None:
            self.frame_ready.emit(frame)
        else:
            self._ensure_fetched(self._mode, ref)
        for step in range(1, _PREFETCH_AHEAD + 1):
            ahead = pos + step * self._direction
            if 0 <= ahead < len(refs):
                self._ensure_fetched(self._mode, refs[ahead])

    def _ensure_mode_index(self, mode: str) -> None:
        if mode in self._indexed_modes or mode in self._indexing_modes:
            return
        self._indexing_modes.add(mode)
        threading.Thread(target=self._build_mode_index, args=(mode,), daemon=True).start()

    def _build_mode_index(self, mode: str) -> None:
        try:
            cfg = _MODE_CONFIG[mode]
            refs = self._list_day_refs(mode, cfg["product"])
            self._indexes[mode] = refs
            self._indexed_modes.add(mode)
            self._indexes_ready = bool(self._indexed_modes)

            if refs and mode in ("meso1", "meso2"):
                # emit now (bbox still None) so the UI immediately enables the buttons.
                self.meso_sectors_updated.emit(dict(self._meso_bboxes))
                threading.Thread(
                    target=self._fetch_mode_bbox,
                    args=(mode, refs[-1].key),
                    daemon=True,
                ).start()

            if not refs and mode == "conus":
                self.error.emit(
                    f"No GOES-East archive imagery available for {self._date.strftime('%Y-%m-%d')}"
                )
                self.capabilities_loaded.emit([])
                return

            if mode == self._mode:
                self.capabilities_loaded.emit(self._mode_times_iso())
            if self._current_archive_time is not None and mode == self._mode:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: index build failed: %s", exc)
            self.error.emit(f"Satellite index failed: {exc}")
        finally:
            self._indexing_modes.discard(mode)

    def _fetch_mode_bbox(self, mode: str, key: str) -> None:
        """Background: fetch the day's last frame so the map hover preview has
        a bbox. The rendered frame is cached rather than thrown away."""
        ref = next((r for r in reversed(self._indexes.get(mode, [])) if r.key == key), None)
        if ref is not None:
            self._ensure_fetched(mode, ref)

    def _list_day_refs(self, mode: str, product: str) -> list[_FrameRef]:
        refs: list[_FrameRef] = []
        base_url = _S3_URL_TEMPLATE.format(bucket=self._bucket)
        year = self._date.strftime("%Y")
        jday = self._date.strftime("%j")
        sector_token = _MODE_CONFIG[mode].get("sector_token")

        for hour in range(24):
            prefix = f"{product}/{year}/{jday}/{hour:02d}/"
            continuation = None
            while True:
                params = f"?list-type=2&prefix={quote(prefix)}"
                if continuation:
                    params += f"&continuation-token={quote(continuation)}"
                resp = requests.get(f"{base_url}/{params}", timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                for key_el in root.findall(".//{*}Contents/{*}Key"):
                    key = (key_el.text or "").strip()
                    if not key.endswith(".nc"):
                        continue
                    if "C02" not in key:
                        continue
                    if sector_token and sector_token not in key:
                        continue
                    ts = _timestamp_from_key(key)
                    if ts is None:
                        continue
                    refs.append(_FrameRef(timestamp=ts, key=key))
                token_el = root.find("{*}NextContinuationToken")
                if token_el is None:
                    break
                continuation = token_el.text

        refs.sort(key=lambda item: item.timestamp)
        log.info("ArchiveSatelliteFetcher: indexed %s %d frames", product, len(refs))
        return refs

    def _mode_times_iso(self) -> list[str]:
        return [
            ref.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            for ref in self._indexes.get(self._mode, [])
        ]

    def _nearest_pos(self, mode: str, when: datetime) -> Optional[int]:
        """Index of the last frame at or before ``when`` (the first frame if none)."""
        refs = self._indexes.get(mode, [])
        if not refs:
            return None
        lo, hi = 0, len(refs) - 1
        result = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if refs[mid].timestamp <= when:
                result = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _is_wanted(self, mode: str, ref: _FrameRef) -> bool:
        """True while ``ref`` is the current frame or inside the prefetch window."""
        if self._current_archive_time is None:
            return mode in ("meso1", "meso2")   # startup bbox fetch
        if mode != self._mode:
            return mode in ("meso1", "meso2") and self._meso_bboxes.get(int(mode[-1])) is None
        refs = self._indexes.get(mode, [])
        pos = self._nearest_pos(mode, self._current_archive_time)
        if pos is None:
            return False
        lo, hi = sorted((pos, pos + _PREFETCH_AHEAD * self._direction))
        return any(refs[i].timestamp == ref.timestamp for i in range(max(lo, 0), min(hi, len(refs) - 1) + 1))

    def _ensure_fetched(self, mode: str, ref: _FrameRef) -> None:
        cache_key = (mode, ref.timestamp)
        with self._fetch_lock:
            if cache_key in self._pending or cache_key in self._cache:
                return
            self._pending.add(cache_key)
        self.loading_changed.emit(True)
        self._pool.submit(self._fetch_frame, mode, ref)

    def _fetch_frame(self, mode: str, ref: _FrameRef) -> None:
        cache_key = (mode, ref.timestamp)
        try:
            # the queue can fall behind fast scrubbing; skip frames no longer near.
            if not self._is_wanted(mode, ref):
                return
            url = f"{_S3_URL_TEMPLATE.format(bucket=self._bucket)}/{ref.key}"
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            png_bytes, bbox = _render_goes_png(resp.content, mode, _MODE_CONFIG[mode].get("fixed_bbox"))
            frame = SatFrame(mode=mode, timestamp=ref.timestamp, png=png_bytes, bbox=bbox)
            with self._fetch_lock:
                self._cache[cache_key] = frame
                self._evict_locked()

            if mode in ("meso1", "meso2"):
                idx = 1 if mode == "meso1" else 2
                bbox_dict = _bbox_to_dict(bbox)
                if self._meso_bboxes.get(idx) != bbox_dict:
                    self._meso_bboxes[idx] = bbox_dict
                    self.meso_sectors_updated.emit(dict(self._meso_bboxes))

            if mode == self._mode and self._current_archive_time is not None:
                pos = self._nearest_pos(mode, self._current_archive_time)
                if pos is not None and self._indexes[mode][pos].timestamp == ref.timestamp:
                    self.frame_ready.emit(frame)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: frame fetch failed %s %s: %s", mode, ref.timestamp, exc)
            self.error.emit(f"Satellite frame failed: {exc}")
        finally:
            with self._fetch_lock:
                self._pending.discard(cache_key)
                idle = not self._pending
            if idle:
                self.loading_changed.emit(False)

    def _evict_locked(self) -> None:
        """Drop the cached frames farthest in time from the current position."""
        now = self._current_archive_time
        while len(self._cache) > _MAX_CACHE:
            if now is None:
                victim = min(self._cache, key=lambda k: k[1])
            else:
                victim = max(self._cache, key=lambda k: (k[0] != self._mode, abs(k[1] - now)))
            del self._cache[victim]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket_for_date(dt: datetime) -> str:
    return "noaa-goes19" if dt >= _GOES19_EAST_START else "noaa-goes16"


def _timestamp_from_key(key: str) -> Optional[datetime]:
    match = _TIME_RE.search(key)
    if not match:
        return None
    year = int(match.group("year"))
    jday = int(match.group("jday"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=jday - 1)
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _bbox_to_dict(bbox: list[float]) -> dict:
    west, south, east, north = bbox
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
    }




_MERC_R = 6378137.0
# (mode, grid geometry, output size) -> (flat source index per output pixel, valid mask)
_INDEX_CACHE: "OrderedDict[tuple, tuple[np.ndarray, np.ndarray]]" = OrderedDict()
_INDEX_CACHE_SIZE = 4
_INDEX_LOCK = threading.Lock()


@dataclass(frozen=True)
class _GoesGrid:
    lon0: float        # radians
    a: float           # semi-major axis, m
    b: float           # semi-minor axis, m
    H: float           # distance from earth centre to satellite, m
    x0: float          # scan angle of column 0, radians
    dx: float
    nx: int
    y0: float          # scan angle of row 0, radians
    dy: float
    ny: int


def _attr(obj, name, default=None):
    val = obj.attrs.get(name, default)
    if isinstance(val, np.ndarray):
        val = val.reshape(-1)[0]
    if isinstance(val, bytes):
        val = val.decode()
    return val


def _read_goes(nc_bytes: bytes) -> tuple[np.ndarray, _GoesGrid]:
    """Read CMI (as reflectance in 0..1, NaN for fill) and the fixed-grid geometry."""
    import h5py

    with h5py.File(io.BytesIO(nc_bytes), "r") as f:
        cmi = f["CMI"]
        raw = cmi[()]
        if str(_attr(cmi, "_Unsigned", "false")).lower() == "true":
            raw = raw.view(np.uint16)
            fill = np.int16(_attr(cmi, "_FillValue", -1)).view(np.uint16)
        else:
            fill = _attr(cmi, "_FillValue")
        data = raw.astype(np.float32) * np.float32(_attr(cmi, "scale_factor", 1.0))
        data += np.float32(_attr(cmi, "add_offset", 0.0))
        if fill is not None:
            data[raw == fill] = np.nan

        proj = f["goes_imager_projection"]
        a = float(_attr(proj, "semi_major_axis"))
        xv, yv = f["x"], f["y"]
        grid = _GoesGrid(
            lon0=math.radians(float(_attr(proj, "longitude_of_projection_origin"))),
            a=a,
            b=float(_attr(proj, "semi_minor_axis")),
            H=float(_attr(proj, "perspective_point_height")) + a,
            x0=float(_attr(xv, "add_offset")) + float(xv[0]) * float(_attr(xv, "scale_factor")),
            dx=float(_attr(xv, "scale_factor")),
            nx=int(xv.shape[0]),
            y0=float(_attr(yv, "add_offset")) + float(yv[0]) * float(_attr(yv, "scale_factor")),
            dy=float(_attr(yv, "scale_factor")),
            ny=int(yv.shape[0]),
        )
    return data, grid


def _lonlat_to_scan(g: _GoesGrid, lon: np.ndarray, lat: np.ndarray):
    """GOES-R PUG forward projection: geodetic lon/lat (radians) -> scan angles.
    Returns (x, y, visible)."""
    e2 = (g.a * g.a - g.b * g.b) / (g.a * g.a)
    lat_c = np.arctan((g.b * g.b) / (g.a * g.a) * np.tan(lat))
    cos_c = np.cos(lat_c)
    rc = g.b / np.sqrt(1.0 - e2 * cos_c * cos_c)
    dlon = lon - g.lon0
    sx = g.H - rc * cos_c * np.cos(dlon)
    sy = -rc * cos_c * np.sin(dlon)
    sz = rc * np.sin(lat_c)
    x = np.arcsin(-sy / np.sqrt(sx * sx + sy * sy + sz * sz))
    y = np.arctan(sz / sx)
    visible = g.H * (g.H - sx) >= sy * sy + (g.a * g.a) / (g.b * g.b) * sz * sz
    return x, y, visible


def _scan_to_lonlat(g: _GoesGrid, x: float, y: float) -> Optional[tuple[float, float]]:
    """Inverse projection for one scan-angle pair; None when it misses the earth."""
    ab2 = (g.a * g.a) / (g.b * g.b)
    qa = math.sin(x) ** 2 + math.cos(x) ** 2 * (math.cos(y) ** 2 + ab2 * math.sin(y) ** 2)
    qb = -2.0 * g.H * math.cos(x) * math.cos(y)
    qc = g.H * g.H - g.a * g.a
    disc = qb * qb - 4.0 * qa * qc
    if disc < 0:
        return None
    rs = (-qb - math.sqrt(disc)) / (2.0 * qa)
    sx = rs * math.cos(x) * math.cos(y)
    sy = -rs * math.sin(x)
    sz = rs * math.cos(x) * math.sin(y)
    lat = math.atan(ab2 * sz / math.sqrt((g.H - sx) ** 2 + sy * sy))
    lon = g.lon0 - math.atan(sy / (g.H - sx))
    return math.degrees(lon), math.degrees(lat)


def _grid_bbox(g: _GoesGrid) -> list[float]:
    """Lon/lat bbox of the grid's corner pixels (meso files carry no geospatial_* attrs)."""
    pts = [
        _scan_to_lonlat(g, g.x0 + i * g.dx, g.y0 + j * g.dy)
        for i in (0, g.nx - 1) for j in (0, g.ny - 1)
    ]
    pts = [p for p in pts if p is not None]
    if not pts:
        raise RuntimeError("GOES file is missing geospatial bounds")
    lons, lats = zip(*pts)
    return [min(lons), min(lats), max(lons), max(lats)]


def _merc_y(lat_deg: float) -> float:
    return _MERC_R * math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def _output_size(mode: str, bbox: list[float]) -> tuple[int, int]:
    west, south, east, north = bbox
    width = _OUTPUT_WIDTH.get(mode, 1100)
    x_span = math.radians(max(east - west, 1e-6)) * _MERC_R
    y_span = max(_merc_y(north) - _merc_y(south), 1e-6)
    return width, max(1, int(round(width * y_span / x_span)))


def _block_factor(g: _GoesGrid, bbox: list[float], width: int) -> int:
    """Source pixels per output pixel (rounded down), measured across the bbox centre.
    The source is box-averaged by this factor so downsampling doesn't alias."""
    west, south, east, north = bbox
    lat = math.radians((south + north) / 2)
    xs, _, _ = _lonlat_to_scan(
        g, np.radians(np.array([west, east])), np.array([lat, lat])
    )
    src_cols = abs(float(xs[1] - xs[0])) / g.dx
    return max(1, int(src_cols / width))


def _resample_index(g: _GoesGrid, k: int, bbox: list[float], size: tuple[int, int], key: tuple):
    with _INDEX_LOCK:
        hit = _INDEX_CACHE.get(key)
        if hit is not None:
            _INDEX_CACHE.move_to_end(key)
            return hit

    west, south, east, north = bbox
    width, height = size
    # web mercator pixel centres: equal steps in x (lon) and in mercator y
    lon = np.radians(west + (np.arange(width) + 0.5) * (east - west) / width)
    my_top, my_bot = _merc_y(north), _merc_y(south)
    my = my_top - (np.arange(height) + 0.5) * (my_top - my_bot) / height
    lat = 2.0 * np.arctan(np.exp(my / _MERC_R)) - math.pi / 2
    lon2, lat2 = np.meshgrid(lon, lat)
    x, y, visible = _lonlat_to_scan(g, lon2, lat2)

    nxk, nyk = g.nx // k, g.ny // k
    # x0/y0 are pixel centres; block i spans source pixels [i*k, (i+1)*k)
    col = np.floor((x - g.x0 + 0.5 * g.dx) / (g.dx * k)).astype(np.int64)
    row = np.floor((y - g.y0 + 0.5 * g.dy) / (g.dy * k)).astype(np.int64)
    valid = visible & (col >= 0) & (col < nxk) & (row >= 0) & (row < nyk)
    flat = np.where(valid, row * nxk + col, 0).astype(np.int32).ravel()
    result = (flat, valid.ravel())

    with _INDEX_LOCK:
        _INDEX_CACHE[key] = result
        while len(_INDEX_CACHE) > _INDEX_CACHE_SIZE:
            _INDEX_CACHE.popitem(last=False)
    return result


def _box_average(data: np.ndarray, k: int) -> np.ndarray:
    """NaN-aware k x k block mean; trailing rows/cols that don't fill a block are dropped."""
    if k == 1:
        return data
    ny, nx = (data.shape[0] // k) * k, (data.shape[1] // k) * k
    blocks = data[:ny, :nx].reshape(ny // k, k, nx // k, k)
    valid = np.isfinite(blocks)
    total = np.where(valid, blocks, 0.0).sum(axis=(1, 3))
    count = valid.sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan).astype(np.float32)


def _encode_grey_alpha(grey: np.ndarray, alpha: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.dstack([grey, alpha]), "LA").save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def _render_goes_png(
    nc_bytes: bytes,
    mode: str,
    fallback_bbox: Optional[list[float]] = None,
) -> tuple[bytes, list[float]]:
    """Resample a GOES CMI file onto a Web Mercator grid covering its bbox and
    encode it as a grey + alpha PNG."""
    data, g = _read_goes(nc_bytes)
    bbox = list(fallback_bbox) if fallback_bbox else _grid_bbox(g)
    size = _output_size(mode, bbox)
    k = _block_factor(g, bbox, size[0])
    key = (mode, g, k, tuple(round(v, 4) for v in bbox), size)
    flat, valid = _resample_index(g, k, bbox, size, key)

    src = _box_average(data, k).ravel()
    vals = src[flat]
    valid = valid & np.isfinite(vals)
    # gamma 0.5 to match the visual brightness of the live WMS tiles.
    grey = np.sqrt(np.clip(np.where(valid, vals, 0.0), 0.0, 1.0))
    grey = (grey * 255.0 + 0.5).astype(np.uint8).reshape(size[1], size[0])
    alpha = np.where(valid, 255, 0).astype(np.uint8).reshape(size[1], size[0])
    return _encode_grey_alpha(grey, alpha), bbox
