import bisect
import logging
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from PyQt6.QtCore import QObject, pyqtSignal

from archive.fetchers import goes_scan
from archive.session import session_bounds
from core import package_sources
from core.qt_signals import emit_alive
from data.endpoints import GOES16_BUCKET, GOES19_BUCKET, s3_bucket_url

log = logging.getLogger(__name__)

_GOES19_EAST_START = datetime(2025, 4, 4, 15, 0, tzinfo=timezone.utc)
# the first GOES-16 Cloud and Moisture Imagery on AWS (2017 day 059)
_GOES16_FIRST = datetime(2017, 2, 28, tzinfo=timezone.utc)
_CHANNEL_RE = re.compile(r"-M\d(C\d{2})_")
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
# band 2 (0.64 um) reflectance, band 13 (10.3 um) brightness temperature
_BAND_CONFIG = {
    "vis": {"channel": "C02", "label": "VIS"},
    "ir": {"channel": "C13", "label": "IR"},
}
_MAX_CACHE = 24
_FETCH_WORKERS = 2
# frames fetched ahead of the current one, in playback direction
_PREFETCH_AHEAD = 2
# the MESO buttons' hover boxes are looked up again once the clock is this
# far from the frames they came from: the sectors are moved through the day
_MESO_BBOX_STALE = timedelta(minutes=30)


@dataclass(frozen=True)
class _FrameRef:
    timestamp: datetime
    key: str


class SatFrame:
    __slots__ = ("mode", "timestamp", "png", "bbox", "band")

    def __init__(self, mode: str, timestamp: datetime, png: bytes, bbox: list[float], band: str = "vis"):
        self.mode = mode
        self.timestamp = timestamp
        self.png = png
        self.bbox = bbox
        self.band = band

    @property
    def frame_id(self) -> str:
        return f"{self.band}-{self.timestamp:%Y%m%dT%H%M%S}"

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%MZ")

    @property
    def time_iso(self) -> str:
        return self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ArchiveSatelliteFetcher(QObject):
    """
    Historical GOES-East imagery backed by NOAA's public AWS archive.

    Supported modes, each in band 2 (visible) or band 13 (infrared):
      - conus  -> ABI-L2-CMIPC
      - meso1  -> ABI-L2-CMIPM sector M1
      - meso2  -> ABI-L2-CMIPM sector M2

    Nothing is listed or downloaded until the layer is turned on
    (``set_active``). Days with no GOES-16 yet (before 28 Feb 2017) or no
    imagery report ``unavailable`` rather than an error.
    """

    frame_ready = pyqtSignal(object)          # SatFrame
    capabilities_loaded = pyqtSignal(list)    # list[str] for current mode
    loading_changed = pyqtSignal(bool)
    error = pyqtSignal(str)
    unavailable = pyqtSignal(str)             # why the day has no imagery
    meso_sectors_updated = pyqtSignal(object)  # {1: bbox|None, 2: bbox|None}

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = _ensure_utc(session_date)
        self._bucket = _bucket_for_date(self._date)
        self._mode = ""   # no mode until user explicitly selects one
        self._band = "vis"
        self._active = False
        # mode -> band -> frames; one listing indexes both bands
        self._indexes: dict[str, dict[str, list[_FrameRef]]] = {
            mode: {band: [] for band in _BAND_CONFIG} for mode in _MODE_CONFIG
        }
        self._indexed_modes: set[str] = set()
        self._indexing_modes: set[str] = set()
        self._cache: dict[tuple[str, str, datetime], SatFrame] = {}
        self._pending: set[tuple[str, str, datetime]] = set()
        self._fetch_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=_FETCH_WORKERS, thread_name_prefix="sat-archive")
        self._current_archive_time: Optional[datetime] = None
        self._direction = 1   # +1 playing forward, -1 scrubbing backward
        self._meso_bboxes: dict[int, Optional[dict]] = {1: None, 2: None}
        self._meso_bbox_times: dict[int, Optional[datetime]] = {1: None, 2: None}
        self._meso_bbox_busy = False
        self._meso_bbox_tried: Optional[datetime] = None   # the clock time last looked up
        self.unavailable_reason = ""
        if session_bounds(self._date)[1] <= _GOES16_FIRST:
            self.unavailable_reason = "No GOES-16 imagery before 28 Feb 2017"

    @property
    def band(self) -> str:
        return self._band

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        """Turn the layer on (index the day, fetch frames) or off (fetch nothing)."""
        self._active = bool(active)
        if not self._active:
            return
        self.load_capabilities()
        if self._mode and self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def load_capabilities(self) -> None:
        if self.unavailable_reason:
            self.unavailable.emit(self.unavailable_reason)
            return
        # the MESO buttons' hover boxes first: one hour's listing, not the day's
        self._refresh_meso_bboxes()
        for mode in _MODE_CONFIG:
            self._ensure_mode_index(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in _MODE_CONFIG:
            return
        self._mode = mode
        self._refresh_selection()

    def set_band(self, band: str) -> None:
        if band not in _BAND_CONFIG or band == self._band:
            return
        self._band = band
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        if not self._active or self.unavailable_reason:
            return
        self._ensure_mode_index(self._mode)
        if self._mode in self._indexed_modes:
            self.capabilities_loaded.emit(self._mode_times_iso())
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def _refs(self, mode: str, band: Optional[str] = None) -> list[_FrameRef]:
        return self._indexes.get(mode, {}).get(band or self._band, [])

    def on_time_changed(self, archive_time: datetime) -> None:
        archive_time = _ensure_utc(archive_time)
        if self._current_archive_time is not None and archive_time != self._current_archive_time:
            self._direction = 1 if archive_time > self._current_archive_time else -1
        self._current_archive_time = archive_time
        if not self._active:
            return
        if not self.unavailable_reason and self._meso_bboxes_stale(archive_time):
            self._refresh_meso_bboxes()
        band = self._band
        refs = self._refs(self._mode, band)
        pos = self._nearest_pos(self._mode, archive_time, band)
        if pos is None:
            return
        ref = refs[pos]
        frame = self._cache.get((self._mode, band, ref.timestamp))
        if frame is not None:
            self.frame_ready.emit(frame)
        else:
            self._ensure_fetched(self._mode, band, ref)
        for step in range(1, _PREFETCH_AHEAD + 1):
            ahead = pos + step * self._direction
            if 0 <= ahead < len(refs):
                self._ensure_fetched(self._mode, band, refs[ahead])

    def _ensure_mode_index(self, mode: str) -> None:
        if mode not in _MODE_CONFIG or mode in self._indexed_modes or mode in self._indexing_modes:
            return
        # one listing indexes every mode of its product (both MESO sectors)
        product = _MODE_CONFIG[mode]["product"]
        modes = [m for m, cfg in _MODE_CONFIG.items()
                 if cfg["product"] == product and m not in self._indexed_modes]
        self._indexing_modes.update(modes)
        threading.Thread(target=self._build_product_index, args=(product, modes), daemon=True).start()

    def _build_product_index(self, product: str, modes: list[str]) -> None:
        try:
            keys = self._list_keys(product, self._session_hours())
            for mode in modes:
                self._index_mode(mode, self._refs_from_keys(keys, _MODE_CONFIG[mode].get("sector_token")))
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: index build failed: %s", exc)
            emit_alive(self, "error", f"Satellite index failed: {exc}")
        finally:
            self._indexing_modes.difference_update(modes)

    def _index_mode(self, mode: str, by_band: dict[str, list[_FrameRef]]) -> None:
        self._indexes[mode] = by_band
        self._indexed_modes.add(mode)
        log.info("ArchiveSatelliteFetcher: indexed %s %s", mode,
                 ", ".join(f"{len(r)} {b}" for b, r in by_band.items()))

        if any(by_band.values()) and mode in ("meso1", "meso2"):
            # emit now (bbox may still be None) so the UI enables the buttons
            emit_alive(self, "meso_sectors_updated", dict(self._meso_bboxes))

        if mode == "conus" and not any(by_band.values()):
            self.unavailable_reason = (
                f"No GOES-East imagery for {self._date.strftime('%Y-%m-%d')}"
            )
            emit_alive(self, "unavailable", self.unavailable_reason)
            emit_alive(self, "capabilities_loaded", [])
            return

        if mode == self._mode:
            emit_alive(self, "capabilities_loaded", self._mode_times_iso())
        if self._current_archive_time is not None and mode == self._mode:
            self.on_time_changed(self._current_archive_time)

    # -- the MESO buttons' hover boxes ---------------------------------------------------

    def _meso_bboxes_stale(self, when: datetime) -> bool:
        """A box is missing or far from ``when``, and wasn't just looked up
        near it (a sector with no frames then stays missing)."""
        tried = self._meso_bbox_tried
        if tried is not None and abs(tried - when) <= _MESO_BBOX_STALE:
            return False
        return any(t is None or abs(t - when) > _MESO_BBOX_STALE for t in self._meso_bbox_times.values())

    def _set_meso_bbox(self, idx: int, bbox: list[float], when: datetime) -> None:
        self._meso_bbox_times[idx] = when
        bbox_dict = _bbox_to_dict(bbox)
        if self._meso_bboxes.get(idx) != bbox_dict:
            self._meso_bboxes[idx] = bbox_dict
            emit_alive(self, "meso_sectors_updated", dict(self._meso_bboxes))

    def _refresh_meso_bboxes(self) -> None:
        with self._fetch_lock:
            if self._meso_bbox_busy:
                return
            self._meso_bbox_busy = True
        threading.Thread(target=self._fetch_meso_bboxes, daemon=True).start()

    def _fetch_meso_bboxes(self) -> None:
        """Background: each MESO sector's box from its infrared frame (the
        smaller file) nearest the clock, so hovering a MESO button shows
        where the sector was then, before any of its frames is fetched.
        Before the day is indexed, the clock's hour alone is listed."""
        try:
            while self._active:
                when = self._current_archive_time or session_bounds(self._date)[0]
                if not self._meso_bboxes_stale(when):
                    break
                self._meso_bbox_tried = when
                hour_keys = None
                for idx in (1, 2):
                    seen = self._meso_bbox_times[idx]
                    if seen is not None and abs(seen - when) <= _MESO_BBOX_STALE:
                        continue
                    mode = f"meso{idx}"
                    if mode in self._indexed_modes:
                        by_band = self._indexes[mode]
                    else:
                        if hour_keys is None:
                            hour = when.replace(minute=0, second=0, microsecond=0)
                            hour_keys = self._list_keys(_MODE_CONFIG[mode]["product"], [hour])
                        by_band = self._refs_from_keys(hour_keys, _MODE_CONFIG[mode].get("sector_token"))
                    refs = by_band.get("ir") or by_band.get("vis") or []
                    if not refs:
                        continue
                    pos = max(0, bisect.bisect_right([r.timestamp for r in refs], when) - 1)
                    ref = refs[pos]
                    if ref.timestamp == seen:
                        continue                  # still the nearest frame
                    scan = goes_scan.load_scan(f"{s3_bucket_url(self._bucket)}/{ref.key}", mode, None)
                    self._set_meso_bbox(idx, scan.bbox, ref.timestamp)
        except Exception as exc:  # noqa: BLE001 -- only the hover preview goes without
            log.warning("ArchiveSatelliteFetcher: MESO sector boxes failed: %s", exc)
        finally:
            with self._fetch_lock:
                self._meso_bbox_busy = False

    # -- listings ------------------------------------------------------------------------

    def _session_hours(self) -> list[datetime]:
        """The session's UTC day plus the next morning up to its cap
        (archive/session.py); the next day can be in a new year."""
        start, cap = session_bounds(self._date)
        hours = []
        hour_start = max(start, _GOES16_FIRST)
        while hour_start < cap:
            hours.append(hour_start)
            hour_start += timedelta(hours=1)
        return hours

    def _list_keys(self, product: str, hours: list[datetime]) -> list[str]:
        """Every .nc key of ``product`` in the given hours."""
        base_url = s3_bucket_url(self._bucket)
        keys = []
        for hour_start in hours:
            prefix = f"{product}/{hour_start:%Y}/{hour_start:%j}/{hour_start:%H}/"
            continuation = None
            while True:
                params = f"?list-type=2&prefix={quote(prefix)}"
                if continuation:
                    params += f"&continuation-token={quote(continuation)}"
                resp = package_sources.requests_get("satellite", f"{base_url}/{params}", timeout=goes_scan.REQUEST_TIMEOUT)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                for key_el in root.findall(".//{*}Contents/{*}Key"):
                    key = (key_el.text or "").strip()
                    if key.endswith(".nc"):
                        keys.append(key)
                token_el = root.find("{*}NextContinuationToken")
                if token_el is None:
                    break
                continuation = token_el.text
        return keys

    @staticmethod
    def _refs_from_keys(keys: list[str], sector_token: Optional[str]) -> dict[str, list[_FrameRef]]:
        """Each band's frames among ``keys`` (one sector's, if given), oldest first."""
        refs: dict[str, list[_FrameRef]] = {band: [] for band in _BAND_CONFIG}
        channels = {cfg["channel"]: band for band, cfg in _BAND_CONFIG.items()}
        for key in keys:
            match = _CHANNEL_RE.search(key)
            band = channels.get(match.group(1)) if match else None
            if band is None:
                continue
            if sector_token and sector_token not in key:
                continue
            ts = _timestamp_from_key(key)
            if ts is None:
                continue
            refs[band].append(_FrameRef(timestamp=ts, key=key))
        for band_refs in refs.values():
            band_refs.sort(key=lambda item: item.timestamp)
        return refs

    def _list_day_refs(self, mode: str, product: str) -> dict[str, list[_FrameRef]]:
        return self._refs_from_keys(self._list_keys(product, self._session_hours()),
                                    _MODE_CONFIG[mode].get("sector_token"))

    def _mode_times_iso(self) -> list[str]:
        return [
            ref.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            for ref in self._refs(self._mode)
        ]

    def _nearest_pos(self, mode: str, when: datetime, band: Optional[str] = None) -> Optional[int]:
        """Index of the last frame at or before ``when`` (the first frame if none)."""
        refs = self._refs(mode, band)
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

    def _is_wanted(self, mode: str, band: str, ref: _FrameRef) -> bool:
        """True while ``ref`` is the current frame or inside the prefetch window."""
        if not self._active or self._current_archive_time is None:
            return False
        if mode != self._mode or band != self._band:
            return False
        refs = self._refs(mode, band)
        pos = self._nearest_pos(mode, self._current_archive_time, band)
        if pos is None:
            return False
        lo, hi = sorted((pos, pos + _PREFETCH_AHEAD * self._direction))
        return any(refs[i].timestamp == ref.timestamp for i in range(max(lo, 0), min(hi, len(refs) - 1) + 1))

    def _ensure_fetched(self, mode: str, band: str, ref: _FrameRef) -> None:
        cache_key = (mode, band, ref.timestamp)
        with self._fetch_lock:
            if cache_key in self._pending or cache_key in self._cache:
                return
            self._pending.add(cache_key)
        emit_alive(self, "loading_changed", True)      # also reached from _fetch_mode_bbox's thread
        self._pool.submit(self._fetch_frame, mode, band, ref)

    def _fetch_frame(self, mode: str, band: str, ref: _FrameRef) -> None:
        cache_key = (mode, band, ref.timestamp)
        try:
            # the queue can fall behind fast scrubbing; skip frames no longer near.
            if not self._is_wanted(mode, band, ref):
                return
            url = f"{s3_bucket_url(self._bucket)}/{ref.key}"
            fixed_bbox = _MODE_CONFIG[mode].get("fixed_bbox")
            scan = goes_scan.load_scan(url, mode, fixed_bbox, ranged=fixed_bbox is not None and band == "vis")
            png_bytes = goes_scan.render_scan(scan, band)
            frame = SatFrame(mode=mode, timestamp=ref.timestamp, png=png_bytes, bbox=scan.bbox, band=band)
            with self._fetch_lock:
                self._cache[cache_key] = frame
                self._evict_locked()

            if mode in ("meso1", "meso2"):
                self._set_meso_bbox(1 if mode == "meso1" else 2, scan.bbox, ref.timestamp)

            if mode == self._mode and band == self._band and self._current_archive_time is not None:
                pos = self._nearest_pos(mode, self._current_archive_time, band)
                if pos is not None and self._refs(mode, band)[pos].timestamp == ref.timestamp:
                    emit_alive(self, "frame_ready", frame)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: frame fetch failed %s %s %s: %s", mode, band, ref.timestamp, exc)
            emit_alive(self, "error", f"Satellite frame failed: {exc}")
        finally:
            with self._fetch_lock:
                self._pending.discard(cache_key)
                idle = not self._pending
            if idle:
                emit_alive(self, "loading_changed", False)

    def _evict_locked(self) -> None:
        """Drop the cached frames farthest in time from the current position,
        other sectors and bands first."""
        now = self._current_archive_time
        while len(self._cache) > _MAX_CACHE:
            if now is None:
                victim = min(self._cache, key=lambda k: k[2])
            else:
                victim = max(self._cache, key=lambda k: ((k[0], k[1]) != (self._mode, self._band), abs(k[2] - now)))
            del self._cache[victim]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket_for_date(dt: datetime) -> str:
    return GOES19_BUCKET if dt >= _GOES19_EAST_START else GOES16_BUCKET


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
