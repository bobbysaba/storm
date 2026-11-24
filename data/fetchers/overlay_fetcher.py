"""
GeoJSON overlays from storm.toml: the ``[[sources]]`` of kind ``overlay``
(data/sources.py), and a code provider's overlay sources. Each is fetched when turned on and then every refresh_min
while it's on; the map draws it (ui/map/widget.py set_source_overlay).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import settings
from data.sources import Source

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 STORM/1.0", "Accept": "application/geo+json, application/json"}
_GEOMETRIES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
               "MultiPolygon", "GeometryCollection"}


class OverlayError(RuntimeError):
    pass


def feature_collection(data) -> dict:
    """A GeoJSON FeatureCollection, Feature or bare geometry as a
    FeatureCollection; anything else raises OverlayError."""
    if not isinstance(data, dict):
        raise OverlayError("not GeoJSON (expected an object)")
    kind = data.get("type")
    if kind == "FeatureCollection" and isinstance(data.get("features"), list):
        return data
    if kind == "Feature":
        return {"type": "FeatureCollection", "features": [data]}
    if kind in _GEOMETRIES:
        return {"type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": data, "properties": {}}]}
    raise OverlayError(f"not GeoJSON (type {kind!r})")


def fetch_overlay(source: Source) -> dict:
    """One overlay source's GeoJSON as a FeatureCollection."""
    if source.fetch is not None:
        return feature_collection(source.fetch())
    url, headers = source.request(source.url)
    req = Request(url, headers={**_HEADERS, **headers})
    try:
        with urlopen(req, timeout=30, context=source.ssl_context(source.url)) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise OverlayError(f"HTTP {exc.code} from {source.url}") from None
    except URLError as exc:
        raise OverlayError(f"network error from {source.url}: {exc.reason}") from None
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except ValueError:
        raise OverlayError(f"{source.url} is not JSON") from None
    return feature_collection(data)


class OverlayFetcher(QObject):
    """Fetches the overlay sources that are on, each every refresh_min."""

    overlay_ready = pyqtSignal(str, object)   # source id, FeatureCollection
    error = pyqtSignal(str, str)              # source id, message

    def __init__(self, parent=None, sources: tuple[Source, ...] | None = None):
        super().__init__(parent)
        if sources is None:
            sources = settings.SETTINGS.sources_of("overlay")
        self.sources: dict[str, Source] = {s.id: s for s in sources}
        self._enabled: set[str] = set()
        self._last: dict[str, datetime] = {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._fetch_due)

    def set_enabled(self, key: str, on: bool) -> None:
        if key not in self.sources:
            return
        if on:
            self._enabled.add(key)
            self._fetch(key)
        else:
            self._enabled.discard(key)
        if self._enabled:
            self._timer.start()
        else:
            self._timer.stop()

    def _fetch_due(self) -> None:
        now = datetime.now(timezone.utc)
        for key in list(self._enabled):
            last = self._last.get(key)
            if last is None or (now - last).total_seconds() >= self.sources[key].refresh_min * 60 - 10:
                self._fetch(key)

    def _fetch(self, key: str) -> None:
        with self._lock:
            if key in self._busy:
                return
            self._busy.add(key)
        threading.Thread(target=self._worker, args=(key,), daemon=True,
                         name=f"overlay-{key}").start()

    def _worker(self, key: str) -> None:
        source = self.sources[key]
        try:
            data = fetch_overlay(source)
            self._last[key] = datetime.now(timezone.utc)
            if key in self._enabled:
                self.overlay_ready.emit(key, data)
        except Exception as exc:  # noqa: BLE001 - a bad overlay mustn't stop the app
            log.warning("overlay %s: %s", key, exc)
            # try again on the next due tick, not every minute
            self._last[key] = datetime.now(timezone.utc)
            self.error.emit(key, f"{source.short}: {exc}")
        finally:
            with self._lock:
                self._busy.discard(key)
