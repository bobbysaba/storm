"""A contours source's runs, fields and tile files (kind = "contours",
https://bobbysaba.com/docs/storm/ref/your-sources/#contours-contour-lines-from-vector-tiles).

The source's url is a folder laid out as:

    index.json               {"runs": ["20260507_15", ...]}  (any order)
    <run>/metadata.json      {"bounds": [w, s, e, n], "variables": {<id>: {...}}}
    <run>/<id>.pmtiles       one vector tile set per field (or .mbtiles,
                             with tiles = "mbtiles")

Each variable may give label, long_name, units and group (the drawer's
column). A tile file is downloaded whole, kept in a cache for a day, and
its tiles served to the map from disk (ui/map/tile_scheme_handler.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from core import mbtiles, pmtiles
from data.sources import Source

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 STORM/1.0"
_CACHE_DIR = Path(tempfile.gettempdir()) / "storm_contour_tiles"
_CACHE_MAX_AGE_S = 24 * 3600
_CACHE_MAX_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class ContourRun:
    time_id: str
    label: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class ContourVariable:
    variable_id: str
    label: str
    long_name: str = ""
    units: str = ""
    group: str = "other"


class ContourFetcher(QObject):
    """Lists a contours source's runs and fields and fetches a field's tiles."""

    times_ready = pyqtSignal(object)             # list[ContourRun], oldest first
    variables_ready = pyqtSignal(str, object)    # time_id, list[ContourVariable]
    overlay_ready = pyqtSignal(object)           # metadata dict for MapLibre
    fetch_error = pyqtSignal(str)

    def __init__(self, source: Source, parent=None):
        super().__init__(parent)
        self.source = source
        self._base_url = source.url.rstrip("/")
        _prune_cache()

    def refresh_catalog(self) -> None:
        threading.Thread(target=self._fetch_times_worker, daemon=True).start()

    def fetch_variables(self, time_id: str) -> None:
        threading.Thread(target=self._fetch_variables_worker,
                         args=(str(time_id or "").strip(),), daemon=True).start()

    def fetch_overlay(self, variable_id: str, time_id: str) -> None:
        threading.Thread(target=self._fetch_overlay_worker,
                         args=(str(variable_id or "").strip(), str(time_id or "").strip()),
                         daemon=True).start()

    def _fetch_times_worker(self) -> None:
        try:
            runs = runs_from_index(self.source, self._fetch_json(_abs_url(self._base_url, "index.json")))
            if not runs:
                raise RuntimeError("index.json lists no runs")
            self.times_ready.emit(runs)
        except Exception as exc:
            log.warning("%s index fetch failed: %s", self.source.id, exc)
            self.fetch_error.emit(f"index: {_friendly_error(exc, self._base_url)}")

    def _fetch_variables_worker(self, time_id: str) -> None:
        if not time_id:
            return
        try:
            variables = variables_from_metadata(self._fetch_json(self._run_url(time_id, "metadata.json")))
            if not variables:
                raise RuntimeError("metadata.json lists no variables")
            self.variables_ready.emit(time_id, variables)
        except Exception as exc:
            log.warning("%s variable fetch failed: %s", self.source.id, exc)
            self.fetch_error.emit(f"{time_id}: {_friendly_error(exc, self._base_url)}")

    def _fetch_overlay_worker(self, variable_id: str, time_id: str) -> None:
        if not variable_id or not time_id:
            return
        try:
            metadata = self._fetch_json(self._run_url(time_id, "metadata.json"))
            variables = metadata.get("variables")
            meta = variables.get(variable_id) if isinstance(variables, dict) else None
            variable = _variable(variable_id, meta if isinstance(meta, dict) else {})
            tile_url = self._run_url(time_id, f"{variable_id}.{self.source.tiles}")
            key = tile_key(self.source.id, time_id, variable_id)
            path = self._download(tile_url, key)
            tileset = read_tileset(path)
            bounds = _parse_bounds(tileset.get("bounds") or metadata.get("bounds"))
            if len(bounds) != 4 or not tileset.get("source_layer"):
                raise RuntimeError("tile file has no bounds or vector layer")
            run = _run(self.source, time_id)
            self.overlay_ready.emit({
                "product": variable_id,
                "label": variable.label,
                "long_name": variable.long_name,
                "units": variable.units,
                "group": variable.group,
                "time": time_id,
                "valid_time": str(metadata.get("valid_time") or _iso(run.timestamp)),
                "tile_url": f"storm://app/contourtiles/{key}/{{z}}/{{x}}/{{y}}.pbf",
                "tile_key": key,
                "tile_path": str(path),
                "source_layer": tileset["source_layer"],
                "bounds": bounds,
                "minzoom": int(tileset.get("minzoom", metadata.get("minzoom", 0))),
                "maxzoom": int(tileset.get("maxzoom", metadata.get("maxzoom", 8))),
            })
        except Exception as exc:
            log.warning("%s overlay fetch failed: %s", self.source.id, exc)
            self.fetch_error.emit(f"{variable_id}: {_friendly_error(exc, self._base_url)}")

    def _run_url(self, time_id: str, name: str) -> str:
        return _abs_url(self._base_url, f"{time_id}/{name}")

    def _get(self, url: str) -> bytes:
        full, headers = self.source.request(url)
        req = Request(full, headers={"User-Agent": USER_AGENT, **headers})
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=self.source.ssl_context(url)) as resp:
            return resp.read()

    def _fetch_json(self, url: str) -> dict:
        try:
            payload = json.loads(self._get(url).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"JSON root is not an object: {url}")
        return payload

    def _download(self, url: str, key: str) -> Path:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"{key}.{self.source.tiles}"
        if path.exists() and path.stat().st_size > 0:
            # mark as recently used so pruning keeps overlays that are still shown
            os.utime(path)
            return path
        data = self._get(url)
        if not data:
            raise RuntimeError(f"empty tile file from {url}")
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
        _prune_cache(keep=path)
        return path


def runs_from_index(source: Source, payload: dict) -> list[ContourRun]:
    """index.json's runs that read as times (run_format), oldest first."""
    values = payload.get("runs") or []
    runs = [_run(source, str(v)) for v in values if isinstance(v, str) and v.strip()]
    return sorted((r for r in runs if r.timestamp is not None), key=lambda r: r.timestamp)


def variables_from_metadata(payload: dict) -> list[ContourVariable]:
    values = payload.get("variables")
    if not isinstance(values, dict):
        return []
    # in the file's order: the drawer lists each group's fields that way
    return [_variable(str(var_id), meta if isinstance(meta, dict) else {})
            for var_id, meta in values.items()]


def _variable(variable_id: str, meta: dict) -> ContourVariable:
    return ContourVariable(variable_id,
                           label=str(meta.get("label") or variable_id),
                           long_name=str(meta.get("long_name") or ""),
                           units=str(meta.get("units") or ""),
                           group=str(meta.get("group") or "other"))


def _run(source: Source, time_id: str) -> ContourRun:
    try:
        ts = datetime.strptime(time_id, source.run_format).replace(tzinfo=timezone.utc)
    except ValueError:
        return ContourRun(time_id, time_id)
    return ContourRun(time_id, ts.strftime("%H:%MZ"), ts)


def read_tileset(path: Path) -> dict:
    """bounds, minzoom, maxzoom and source_layer (the first vector layer)
    from a .pmtiles or .mbtiles file."""
    if path.suffix == ".pmtiles":
        f = pmtiles.PMTilesFile(str(path))
        meta = f.metadata()
        layers = meta.get("vector_layers") or []
        return {"bounds": list(f.header.bounds), "minzoom": f.header.min_zoom,
                "maxzoom": f.header.max_zoom,
                "source_layer": str(layers[0].get("id") or "") if layers else ""}
    conn = mbtiles.connect(str(path))
    try:
        values = {str(k): str(v) for k, v in conn.execute("SELECT name, value FROM metadata")}
    finally:
        conn.close()
    try:
        layers = json.loads(values.get("json") or "{}").get("vector_layers") or []
    except (json.JSONDecodeError, AttributeError):
        layers = []
    out = {"bounds": _parse_bounds(values.get("bounds")),
           "source_layer": str(layers[0].get("id") or "") if layers else ""}
    for key in ("minzoom", "maxzoom"):
        if values.get(key, "").strip().isdigit():
            out[key] = int(values[key])
    return out


def tile_key(source_id: str, time_id: str, variable_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{source_id}-{time_id}-{variable_id}").strip("-")
    return safe or "contours"


def _abs_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _parse_bounds(value: object) -> list[float]:
    parts = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    if len(parts) != 4:
        return []
    try:
        return [float(str(p).strip()) for p in parts]
    except ValueError:
        return []


def _iso(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""


def _prune_cache(keep: Path | None = None, now: float | None = None) -> None:
    """Delete cached tile files older than 24 h, then the oldest until under the size cap.

    The scheme handler reopens a file on every tile request, so *keep* (the
    overlay just requested) is never removed.
    """
    if not _CACHE_DIR.is_dir():
        return
    now = time.time() if now is None else now
    entries = []
    for path in _CACHE_DIR.iterdir():
        if not path.is_file() or path == keep:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime > _CACHE_MAX_AGE_S:
            _remove_cache_file(path)
        else:
            entries.append((st.st_mtime, st.st_size, path))

    total = sum(size for _, size, _ in entries)
    if keep is not None and keep.exists():
        total += keep.stat().st_size
    for _, size, path in sorted(entries):
        if total <= _CACHE_MAX_BYTES:
            break
        if _remove_cache_file(path):
            total -= size


def _remove_cache_file(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError as exc:
        # windows refuses to delete a file the tile handler has open; retry next time
        log.debug("contour tile cache: could not remove %s: %s", path.name, exc)
        return False


def _friendly_error(exc: Exception, base_url: str) -> str:
    if isinstance(exc, TimeoutError):
        return f"timeout connecting to {base_url}"
    reason = getattr(exc, "reason", None)
    if isinstance(exc, URLError) and reason:
        return str(reason)
    return str(exc)
