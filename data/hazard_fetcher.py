# data/hazard_fetcher.py
# Fetches SPC Day 1 outlook polygons and NWS active warnings in the background.

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
POLL_INTERVAL_SECONDS = 120

SPC_URLS = {
    "cat": "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.lyr.geojson",
    "wind": "https://www.spc.noaa.gov/products/outlook/day1otlk_wind.lyr.geojson",
    "hail": "https://www.spc.noaa.gov/products/outlook/day1otlk_hail.lyr.geojson",
    "tor": "https://www.spc.noaa.gov/products/outlook/day1otlk_torn.lyr.geojson",
}

NWS_ACTIVE_ALERTS_URL = (
    "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
)
SPC_MD_URL = (
    "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks"
    "/spc_mesoscale_discussion/MapServer/0/query?where=1%3D1&outFields=*&f=geojson"
)


def _empty_fc() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": []}


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def _spc_cat_key(props: dict[str, Any]) -> str:
    txt = " ".join(_norm(v) for v in props.values())
    if "HIGH" in txt:
        return "HIGH"
    if "MDT" in txt or "MODERATE" in txt:
        return "MDT"
    if "ENH" in txt or "ENHANCED" in txt:
        return "ENH"
    if "SLGT" in txt or "SLIGHT" in txt:
        return "SLGHT"
    if "MRGL" in txt or "MARGINAL" in txt:
        return "MRGL"
    return ""


def _feature_bbox(geom: dict[str, Any]) -> tuple[float, float, float, float] | None:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    pts: list[tuple[float, float]] = []
    if not coords:
        return None

    if gtype == "Point":
        pts = [(coords[0], coords[1])]
    elif gtype == "LineString":
        pts = [(c[0], c[1]) for c in coords]
    elif gtype == "Polygon":
        for ring in coords:
            pts.extend((c[0], c[1]) for c in ring)
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                pts.extend((c[0], c[1]) for c in ring)
    else:
        return None

    if not pts:
        return None
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


def _bbox_intersects(a: tuple[float, float, float, float],
                     b: tuple[float, float, float, float]) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)


class HazardFetcher(QObject):
    """
    Polls SPC + NWS hazard feeds in the background.

    Signals:
      spc_received(dict, dict, dict, dict): cat, wind, hail, tor feature collections
      nws_received(dict): warnings feature collection
      spc_watches_received(dict): watch polygons feature collection
      fetch_error(str): recoverable error text
    """

    spc_received = pyqtSignal(object, object, object, object)
    nws_received = pyqtSignal(object)
    spc_watches_received = pyqtSignal(object)
    spc_mds_received = pyqtSignal(object)
    fetch_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._fetch_lock = threading.Lock()

        self._spc_categories = {"MRGL": False, "SLGHT": False, "ENH": False, "MDT": False, "HIGH": False}
        self._spc_products = {"wind": False, "hail": False, "tor": False}
        self._spc_watches_enabled = False
        self._spc_mds_enabled = False
        self._nws_enabled = False

        # default map/bundle extent: lon_min, lat_min, lon_max, lat_max
        self._nws_bbox = (-116.0, 28.0, -82.0, 49.0)

    def set_spc_category_enabled(self, key: str, enabled: bool):
        k = _norm(key)
        if k in self._spc_categories:
            self._spc_categories[k] = bool(enabled)

    def set_spc_product_enabled(self, key: str, enabled: bool):
        k = key.strip().lower()
        if k in self._spc_products:
            self._spc_products[k] = bool(enabled)

    def set_nws_enabled(self, enabled: bool):
        self._nws_enabled = bool(enabled)

    def set_spc_watches_enabled(self, enabled: bool):
        self._spc_watches_enabled = bool(enabled)

    def set_spc_mds_enabled(self, enabled: bool):
        self._spc_mds_enabled = bool(enabled)

    def set_nws_bbox(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float):
        self._nws_bbox = (lon_min, lat_min, lon_max, lat_max)

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()

    def fetch_now(self):
        if not self._fetch_lock.acquire(blocking=False):
            return

        def _run():
            try:
                self._fetch_cycle()
            finally:
                self._fetch_lock.release()

        threading.Thread(target=_run, daemon=True).start()

    def _poll_loop(self):
        while self._running:
            if self._fetch_lock.acquire(blocking=False):
                try:
                    self._fetch_cycle()
                finally:
                    self._fetch_lock.release()
            self._stop_event.wait(POLL_INTERVAL_SECONDS)
            self._stop_event.clear()

    def _fetch_cycle(self):
        try:
            if any(self._spc_categories.values()) or any(self._spc_products.values()):
                self._fetch_spc()
            if self._spc_watches_enabled:
                self._fetch_spc_watches()
            if self._spc_mds_enabled:
                self._fetch_spc_mds()
            if self._nws_enabled:
                self._fetch_nws()
        except Exception as exc:  # guard poll loop
            log.exception("Hazard fetch cycle failed")
            self.fetch_error.emit(f"Hazard fetch failed: {exc}")

    def _get_json(self, url: str) -> dict[str, Any]:
        req = Request(
            url,
            headers={
                "User-Agent": "STORM/1.0 (contact: support)",
                "Accept": "application/geo+json, application/json",
            },
        )
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)

    def _fetch_spc(self):
        cat_fc = _empty_fc()
        wind_fc = _empty_fc()
        hail_fc = _empty_fc()
        tor_fc = _empty_fc()

        try:
            cat_raw = self._get_json(SPC_URLS["cat"])
            feats = []
            for f in cat_raw.get("features", []):
                props = dict(f.get("properties") or {})
                cat = _spc_cat_key(props)
                if not cat:
                    continue
                props["cat"] = cat
                feats.append({
                    "type": "Feature",
                    "geometry": f.get("geometry"),
                    "properties": props,
                })
            cat_fc = {"type": "FeatureCollection", "features": feats}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("SPC categorical fetch failed: %s", exc)
            self.fetch_error.emit(f"SPC categorical fetch failed: {exc}")

        for key in ("wind", "hail", "tor"):
            out = _empty_fc()
            if self._spc_products.get(key, False):
                try:
                    raw = self._get_json(SPC_URLS[key])
                    feats = []
                    for f in raw.get("features", []):
                        feats.append({
                            "type": "Feature",
                            "geometry": f.get("geometry"),
                            "properties": dict(f.get("properties") or {}),
                        })
                    out = {"type": "FeatureCollection", "features": feats}
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                    log.warning("SPC %s fetch failed: %s", key, exc)
                    self.fetch_error.emit(f"SPC {key} fetch failed: {exc}")
            if key == "wind":
                wind_fc = out
            elif key == "hail":
                hail_fc = out
            else:
                tor_fc = out

        self.spc_received.emit(cat_fc, wind_fc, hail_fc, tor_fc)

    def _fetch_nws(self):
        out = _empty_fc()
        try:
            raw = self._get_json(NWS_ACTIVE_ALERTS_URL)
            feats = []
            for f in raw.get("features", []):
                props = dict(f.get("properties") or {})
                event = str(props.get("event", "")).lower()
                if "warning" not in event:
                    continue
                geom = f.get("geometry")
                if not geom:
                    continue
                bb = _feature_bbox(geom)
                if bb is None or not _bbox_intersects(bb, self._nws_bbox):
                    continue
                props["nws_color"] = _nws_color_for_event(event)
                feats.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": props,
                })
            out = {"type": "FeatureCollection", "features": feats}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("NWS warnings fetch failed: %s", exc)
            self.fetch_error.emit(f"NWS warnings fetch failed: {exc}")
        self.nws_received.emit(out)

    def _fetch_spc_watches(self):
        out = _empty_fc()
        try:
            raw = self._get_json(NWS_ACTIVE_ALERTS_URL)
            feats = []
            for f in raw.get("features", []):
                props = dict(f.get("properties") or {})
                event = str(props.get("event", "")).lower()
                if "watch" not in event:
                    continue
                geom = f.get("geometry")
                if not geom:
                    continue
                props["watch_color"] = "#FF0000" if "tornado watch" in event else "#FFD700"
                feats.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": props,
                })
            out = {"type": "FeatureCollection", "features": feats}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("SPC watches fetch failed: %s", exc)
            self.fetch_error.emit(f"SPC watches fetch failed: {exc}")
        self.spc_watches_received.emit(out)


    def _fetch_spc_mds(self):
        out = _empty_fc()
        try:
            raw = self._get_json(SPC_MD_URL)
            feats = []
            for f in raw.get("features", []):
                props = dict(f.get("properties") or {})
                # The endpoint returns a tiny "NoArea" placeholder when no MDs are active.
                if str(props.get("name", "")).strip().lower() in ("noarea", "no area", ""):
                    continue
                feats.append({
                    "type": "Feature",
                    "geometry": f.get("geometry"),
                    "properties": props,
                })
            out = {"type": "FeatureCollection", "features": feats}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("SPC MD fetch failed: %s", exc)
            self.fetch_error.emit(f"SPC MD fetch failed: {exc}")
        self.spc_mds_received.emit(out)


def _nws_color_for_event(event: str) -> str:
    # Keep common NWS warning colors used in public products.
    if "tornado" in event:
        return "#FF0000"
    if "severe thunderstorm" in event:
        return "#FFA500"
    if "flash flood" in event:
        return "#00FF00"
    if "flood warning" in event:
        return "#00FF7F"
    if "winter storm" in event:
        return "#FF69B4"
    if "blizzard" in event:
        return "#FF4500"
    return "#FFD700"
