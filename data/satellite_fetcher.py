# data/satellite_fetcher.py
# Polls IEM GOES-East WMS for sector metadata and downloads satellite imagery
# frames as single GetMap PNG images, caching up to MAX_FRAMES per mode.
# IEM's goes_east.cgi dynamically serves the current operational GOES-East
# satellite (GOES-19 as of 2025), so no satellite-specific URL changes are needed.
# Mirrors the radar caching model so the UI can offer identical playback controls.

import base64
import logging
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

IEM_WMS  = "https://mesonet.agron.iastate.edu/cgi-bin/wms/goes_east.cgi"
CAPS_URL = IEM_WMS + "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetCapabilities"

CAPS_POLL_MS  = 5 * 60 * 1000   # 5 min — matches CONUS scan cadence
CONUS_POLL_MS = 5 * 60 * 1000
MESO_POLL_MS  =      60 * 1000  # 1 min — matches MESO scan cadence

MAX_FRAMES      = 10
REQUEST_TIMEOUT = 20

# Fixed CONUS image extent (west, south, east, north) and pixel size.
# Covers the GOES-East CONUS domain at a 2:1 aspect ratio.
CONUS_BBOX   = [-126.0, 22.0, -64.0, 52.0]
CONUS_W, CONUS_H = 1600, 800

# MESO images are square (sector ≈ 1000×1000 km)
MESO_W, MESO_H = 1024, 1024


@dataclass
class SatFrame:
    timestamp: datetime
    b64:       str
    bbox:      list   # [west, south, east, north]

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%MZ")


class SatelliteFetcher(QObject):
    """
    Background poller for GOES-East visible satellite imagery.

    Signals:
        meso_sectors_updated(object)  — {1: bbox_or_None, 2: bbox_or_None}
        frames_updated(str, object)   — (mode, list[SatFrame]) when a new frame
                                        is added to the cache for that mode.
            mode is one of "conus", "meso1", "meso2".
    """

    meso_sectors_updated = pyqtSignal(object)        # dict
    frames_updated       = pyqtSignal(str, object)   # mode, list[SatFrame]

    def __init__(self, parent=None):
        super().__init__(parent)

        self._meso_bboxes: dict[int, dict | None] = {1: None, 2: None}
        self._frames:      dict[str, list]         = {"conus": [], "meso1": [], "meso2": []}
        self._layer_times: dict[str, list[str]]    = {}
        self._lock = threading.Lock()

        # per-key inflight guard so parallel polls don't stack up
        self._inflight: dict[str, bool] = {
            "caps": False, "conus": False, "meso1": False, "meso2": False,
            "conus_hist": False, "meso1_hist": False, "meso2_hist": False
        }

        self._caps_timer  = QTimer(self)
        self._conus_timer = QTimer(self)
        self._meso_timer  = QTimer(self)

        self._caps_timer.timeout.connect(self._poll_caps)
        self._conus_timer.timeout.connect(self._poll_conus)
        self._meso_timer.timeout.connect(self._poll_meso)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        self._caps_timer.start(CAPS_POLL_MS)
        self._conus_timer.start(CONUS_POLL_MS)
        self._meso_timer.start(MESO_POLL_MS)

        # stagger initial fetches to avoid simultaneous TLS handshakes
        QTimer.singleShot(2_000, self._poll_caps)
        QTimer.singleShot(4_000, self._poll_conus)
        log.info("SatelliteFetcher: started")

    def stop(self):
        for t in (self._caps_timer, self._conus_timer, self._meso_timer):
            t.stop()

    def fetch_now(self, mode: str = ""):
        """Trigger an immediate fetch — pass mode="conus"/"meso1"/"meso2" or
        empty string to refresh everything."""
        targets = [mode] if mode else ["caps", "conus", "meso"]
        for m in targets:
            if m == "caps":
                QTimer.singleShot(0, self._poll_caps)
            elif m == "conus":
                QTimer.singleShot(0, self._poll_conus)
            elif m in ("meso", "meso1", "meso2"):
                QTimer.singleShot(0, self._poll_meso)

    def fetch_history(self, mode: str, count: int = MAX_FRAMES):
        """Backfill recent frames for a mode using WMS time positions."""
        mode = (mode or "").strip().lower()
        if mode not in ("conus", "meso1", "meso2"):
            return
        count = max(1, min(int(count), MAX_FRAMES))
        key = f"{mode}_hist"
        self._spawn(key, lambda: self._worker_history(mode, count))

    def frames(self, mode: str) -> list:
        """Return a snapshot of the cached frames for the given mode."""
        with self._lock:
            return list(self._frames.get(mode, []))

    # ── Internal pollers ──────────────────────────────────────────────────────

    def _poll_caps(self):
        self._spawn("caps", self._worker_caps)

    def _poll_conus(self):
        self._spawn("conus", self._worker_conus)

    def _poll_meso(self):
        self._spawn("meso1", lambda: self._worker_meso(1))
        self._spawn("meso2", lambda: self._worker_meso(2))

    def _spawn(self, key: str, fn):
        with self._lock:
            if self._inflight.get(key):
                return
            self._inflight[key] = True
        threading.Thread(target=self._guarded(key, fn), daemon=True).start()

    def _guarded(self, key: str, fn):
        def _run():
            try:
                fn()
            except Exception as exc:
                log.warning("SatelliteFetcher[%s]: %s", key, exc)
            finally:
                with self._lock:
                    self._inflight[key] = False
        return _run

    # ── Workers ───────────────────────────────────────────────────────────────

    def _worker_caps(self):
        with urlopen(CAPS_URL, timeout=REQUEST_TIMEOUT) as resp:
            xml_bytes = resp.read()
        root        = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
        sectors     = _parse_meso_bboxes(root)
        layer_times = _parse_layer_times(root)
        with self._lock:
            self._meso_bboxes = sectors
            self._layer_times = layer_times
        self.meso_sectors_updated.emit(sectors)
        log.debug(
            "SatelliteFetcher: caps MESO-1=%s  MESO-2=%s",
            sectors.get(1), sectors.get(2),
        )

    def _worker_conus(self):
        w, s, e, n = CONUS_BBOX
        url = _wms_url("conus_ch02", w, s, e, n, CONUS_W, CONUS_H)
        b64 = self._fetch_image(url)
        if b64:
            self._push_frame("conus", SatFrame(
                timestamp=datetime.now(timezone.utc),
                b64=b64,
                bbox=list(CONUS_BBOX),
            ))

    def _worker_meso(self, idx: int):
        with self._lock:
            bbox = self._meso_bboxes.get(idx)
        if not bbox:
            return
        w = bbox["west"]; s = bbox["south"]; e = bbox["east"]; n = bbox["north"]
        layer = f"mesoscale-{idx}_ch02"
        url = _wms_url(layer, w, s, e, n, MESO_W, MESO_H)
        b64 = self._fetch_image(url)
        if b64:
            self._push_frame(f"meso{idx}", SatFrame(
                timestamp=datetime.now(timezone.utc),
                b64=b64,
                bbox=[w, s, e, n],
            ))

    def _worker_history(self, mode: str, count: int):
        layer = "conus_ch02" if mode == "conus" else f"mesoscale-{1 if mode == 'meso1' else 2}_ch02"
        with self._lock:
            times = list(self._layer_times.get(layer, []))
            bbox  = CONUS_BBOX if mode == "conus" else self._meso_bboxes.get(1 if mode == "meso1" else 2)

        # Caps may not have arrived yet — fetch them now so we have time positions.
        if not times:
            try:
                self._worker_caps()
            except Exception as exc:
                log.warning("SatelliteFetcher: caps fetch in history worker failed: %s", exc)
            with self._lock:
                times = list(self._layer_times.get(layer, []))
                bbox  = CONUS_BBOX if mode == "conus" else self._meso_bboxes.get(1 if mode == "meso1" else 2)

        if not times or not bbox:
            # fall back to a single fetch if we still don't have time positions
            if mode == "conus":
                self._worker_conus()
            else:
                self._worker_meso(1 if mode == "meso1" else 2)
            return

        times = times[-count:]
        w = bbox[0] if mode == "conus" else bbox["west"]
        s = bbox[1] if mode == "conus" else bbox["south"]
        e = bbox[2] if mode == "conus" else bbox["east"]
        n = bbox[3] if mode == "conus" else bbox["north"]
        width, height = (CONUS_W, CONUS_H) if mode == "conus" else (MESO_W, MESO_H)

        # Fetch oldest→newest so the cache builds in chronological order.
        for tstr in times:
            url = _wms_url(layer, w, s, e, n, width, height, time_str=tstr)
            b64 = self._fetch_image(url)
            if not b64:
                continue
            ts = _parse_time(tstr)
            self._push_frame(mode, SatFrame(
                timestamp=ts,
                b64=b64,
                bbox=[w, s, e, n],
            ))

    def _fetch_image(self, url: str) -> str:
        """Download a WMS GetMap PNG and return it base64-encoded, or "" on error."""
        try:
            req = Request(url, headers={"User-Agent": "STORM/1.0"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                ct = resp.headers.get("Content-Type", "")
                if "image" not in ct:
                    log.warning("SatelliteFetcher: unexpected Content-Type: %s", ct)
                    return ""
                data = resp.read()
            return base64.b64encode(data).decode("ascii")
        except (HTTPError, URLError, Exception) as exc:
            log.warning("SatelliteFetcher: image fetch error: %s", exc)
            return ""

    def _push_frame(self, mode: str, frame: SatFrame):
        with self._lock:
            cache = self._frames.setdefault(mode, [])
            cache.append(frame)
            if len(cache) > MAX_FRAMES:
                cache.pop(0)
            frames_copy = list(cache)
        self.frames_updated.emit(mode, frames_copy)
        log.debug(
            "SatelliteFetcher[%s]: cached %s (%d/%d)",
            mode, frame.time_str, len(frames_copy), MAX_FRAMES,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_meso_bboxes(root: ET.Element) -> dict:
    _strip_ns(root)
    sectors: dict = {1: None, 2: None}
    for layer in root.iter("Layer"):
        name_el = layer.find("Name")
        bbox_el  = layer.find("LatLonBoundingBox")
        if name_el is None or bbox_el is None:
            continue
        name = (name_el.text or "").strip()
        if name == "mesoscale-1_ch02":
            idx = 1
        elif name == "mesoscale-2_ch02":
            idx = 2
        else:
            continue
        try:
            sectors[idx] = {
                "west":  float(bbox_el.attrib["minx"]),
                "south": float(bbox_el.attrib["miny"]),
                "east":  float(bbox_el.attrib["maxx"]),
                "north": float(bbox_el.attrib["maxy"]),
            }
        except (KeyError, ValueError):
            pass
    return sectors


def _parse_layer_times(root: ET.Element) -> dict[str, list[str]]:
    """Extract WMS time positions per layer name, respecting inherited Dimensions."""
    _strip_ns(root)
    out: dict[str, list[str]] = {}

    def _direct_time(layer_el: ET.Element) -> str:
        for dim in layer_el.findall("Dimension"):
            if (dim.attrib.get("name") or "").lower() == "time":
                return (dim.text or "").strip()
        for ext in layer_el.findall("Extent"):
            if (ext.attrib.get("name") or "").lower() == "time":
                return (ext.text or "").strip()
        return ""

    def _walk(layer_el: ET.Element, inherited: str):
        time_text = _direct_time(layer_el) or inherited
        name_el = layer_el.find("Name")
        if name_el is not None and (name_el.text or "").strip() and time_text:
            times = [t.strip() for t in time_text.split(",") if t.strip()]
            if times:
                out[name_el.text.strip()] = times
        for child in layer_el.findall("Layer"):
            _walk(child, time_text)

    cap = root.find("Capability")
    top = cap if cap is not None else root
    for layer in top.findall("Layer"):
        _walk(layer, "")
    return out


def _parse_time(tstr: str) -> datetime:
    s = (tstr or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _wms_url(layer: str, west: float, south: float, east: float, north: float,
             width: int, height: int, time_str: str | None = None) -> str:
    base = (
        f"{IEM_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        f"&LAYERS={layer}&SRS=EPSG:4326"
        f"&BBOX={west},{south},{east},{north}&WIDTH={width}&HEIGHT={height}"
        f"&FORMAT=image/png&TRANSPARENT=TRUE&STYLES="
    )
    if time_str:
        base += f"&TIME={time_str}"
    return base


def _strip_ns(el: ET.Element):
    if "}" in el.tag:
        el.tag = el.tag.split("}", 1)[1]
    for child in el:
        _strip_ns(child)
