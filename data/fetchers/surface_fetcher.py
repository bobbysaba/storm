from __future__ import annotations

import json
import logging
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import settings
from core.observation import Observation
from core.obs_cleanup import float_or_none
from data import endpoints
from data.sources import Source, decode, read_station_obs, read_stations

log = logging.getLogger(__name__)

# iem endpoints for ASOS
IEM_METAR_GEOJSON = endpoints.IEM_METAR_GEOJSON
IEM_CURRENTS_URL  = endpoints.IEM_CURRENTS
IEM_CURRENTS_BATCH = 100   # stations per IEM request
_MISSING = ("--", "M")      # IEM currents' missing-value markers
MAX_ASOS_STATIONS  = 400   # cap to keep map rendering fast
ASOS_REFRESH_MIN   = 5

# the roster shipped with STORM, used until a fresher copy is saved
_ASOS_STATIONS_FILE = pathlib.Path(__file__).parents[1] / "asos_stations.json"
# the copy refreshed from IEM, outside the checkout so `git pull` stays clean
_ASOS_STATIONS_CACHE = pathlib.Path.home() / ".cache" / "storm" / "asos_stations.json"
ASOS_ROSTER_MAX_AGE_S = 30 * 86400


def _read_roster(path: pathlib.Path) -> dict[str, dict] | None:
    try:
        stations = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("ASOS: failed to read %s: %s", path, exc)
        return None
    return stations if isinstance(stations, dict) and stations else None


def load_asos_station_roster() -> dict[str, dict]:
    """The ASOS/METAR station roster (station id -> {lat, lon, name}): the
    copy in ~/.cache/storm while it's under 30 days old, else a fresh one
    from IEM's metar.geojson (saved there), else the stale copy or the one
    shipped in data/asos_stations.json. Shared by live ASOS and the
    archive's ASOS replay (archive/fetchers/asos_archive_fetcher.py)."""
    cached = _read_roster(_ASOS_STATIONS_CACHE)
    if cached is not None:
        try:
            age = datetime.now(timezone.utc).timestamp() - _ASOS_STATIONS_CACHE.stat().st_mtime
        except OSError:
            age = ASOS_ROSTER_MAX_AGE_S
        if age < ASOS_ROSTER_MAX_AGE_S:
            log.info("ASOS: loaded %d stations from %s", len(cached), _ASOS_STATIONS_CACHE)
            return cached
    try:
        stations = _fetch_asos_station_roster()
    except (SurfaceFetchError, RuntimeError) as exc:
        fallback = cached or _read_roster(_ASOS_STATIONS_FILE)
        if fallback is None:
            raise
        log.warning("ASOS: station roster refresh failed, using the saved one: %s", exc)
        return fallback
    try:
        _ASOS_STATIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _ASOS_STATIONS_CACHE.write_text(json.dumps(stations, separators=(",", ":")), "utf-8")
        log.info("ASOS: saved %d stations to %s", len(stations), _ASOS_STATIONS_CACHE)
    except Exception as exc:
        log.warning("ASOS: could not save station file: %s", exc)
    return stations


def _fetch_asos_station_roster() -> dict[str, dict]:
    """The roster built from IEM's metar.geojson."""
    log.info("ASOS: fetching station metadata from IEM metar.geojson …")
    req = Request(IEM_METAR_GEOJSON, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise SurfaceFetchError(f"HTTP {exc.code} from {IEM_METAR_GEOJSON}") from exc
    except URLError as exc:
        raise SurfaceFetchError(f"network error from {IEM_METAR_GEOJSON}: {exc.reason}") from exc
    try:
        fc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SurfaceFetchError(f"ASOS station metadata from {IEM_METAR_GEOJSON} is not JSON") from exc

    stations: dict[str, dict] = {}
    for feat in fc.get("features", []):
        props = feat.get("properties", {})
        stid  = (props.get("station") or props.get("stid") or "").strip().upper()
        if not stid:
            continue
        coords = (feat.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        name = (props.get("name") or stid).strip()
        stations[stid] = {"lat": lat, "lon": lon, "name": name}

    if not stations:
        raise RuntimeError("IEM metar.geojson returned no station features")

    return stations


class SurfaceFetchError(RuntimeError):
    """Recoverable provider error with context useful for field debugging."""


class SurfaceFetcher(QObject):
    """Live surface obs: ASOS (IEM) inside a box drawn on the map, plus the
    station_obs [[sources]] in storm.toml (data/sources.py), each on its own
    refresh_min. Every source is checked once a minute and fetched when due;
    fetch_now() fetches every source that's on."""

    observations_updated = pyqtSignal(object)
    status_updated = pyqtSignal(str)
    diagnostics_updated = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, parent=None, sources: tuple[Source, ...] | None = None):
        super().__init__(parent)
        if sources is None:
            sources = settings.SETTINGS.sources_of("station_obs")
        self.sources: dict[str, Source] = {s.id: s for s in sources}
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._fetch_due)
        self._state_lock = threading.Lock()
        self._running = False
        self._pending_refresh = False
        self._force = False
        self._networks_enabled = {key: False for key in self.sources}
        # source key -> (items, status part, when fetched), for sources not due
        self._last_result: dict[str, tuple[list[dict], str, datetime]] = {}
        # source key -> (stations, when fetched)
        self._station_cache: dict[str, tuple[dict[str, dict], datetime]] = {}
        self._asos_enabled = False
        self._asos_stations: dict[str, dict] | None = None   # stid → {lat, lon, name}
        self._asos_bbox: tuple[float, float, float, float] | None = None
        self._last_good: dict[str, tuple[list[dict], datetime]] = {}
        self._source_diag: dict[str, dict[str, Any]] = {}
        self._headers = {
            "User-Agent": "Mozilla/5.0 STORM/1.0",
            "Accept": "application/json, text/html, application/xml, text/xml",
        }

    def start(self):
        self._update_timer()

    def stop(self):
        self._timer.stop()

    def set_network_enabled(self, key: str, enabled: bool):
        if key not in self.sources:
            return
        self._networks_enabled[key] = enabled
        if not enabled:
            self._last_result.pop(key, None)
        self._update_timer()
        self.fetch_now()

    def network_enabled(self, key: str) -> bool:
        return self._networks_enabled.get(key, False)

    def _update_timer(self):
        if any(self._networks_enabled.values()) or (self._asos_enabled and self._asos_bbox is not None):
            self._timer.start()
        else:
            self._timer.stop()

    def fetch_now(self):
        """Fetch every source that's on, due or not."""
        self._start(force=True)

    def _fetch_due(self):
        self._start(force=False)

    def _start(self, force: bool):
        with self._state_lock:
            self._force = self._force or force
            if self._running:
                self._pending_refresh = True
                return
            self._running = True
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _due(self, key: str, refresh_min: float, now: datetime) -> bool:
        last = self._last_result.get(key)
        # a few seconds' slack so a 5-minute source isn't left for a 6th tick
        return last is None or (now - last[2]).total_seconds() >= refresh_min * 60 - 10

    def _fetch_or_reuse(self, key: str, label: str, refresh_min: float, fetch_fn,
                        force: bool, parts: list[str]) -> list[dict]:
        now = datetime.now(timezone.utc)
        if not force and not self._due(key, refresh_min, now):
            items, part, _ = self._last_result[key]
            parts.append(part)
            return items
        mine: list[str] = []
        items = self._fetch_part(key, label, fetch_fn, mine)
        self._last_result[key] = (items, mine[0], now)
        parts.extend(mine)
        return items

    def _fetch_worker(self):
        try:
            with self._state_lock:
                force, self._force = self._force, False
            payload: list[dict] = []
            parts: list[str] = []

            for key, source in self.sources.items():
                if self._networks_enabled[key]:
                    payload.extend(self._fetch_or_reuse(
                        key, source.short, source.refresh_min,
                        lambda s=source: self._fetch_station_obs(s), force, parts))

            if self._asos_enabled and self._asos_bbox is not None:
                payload.extend(self._fetch_or_reuse(
                    "asos", "ASOS", ASOS_REFRESH_MIN, self._fetch_asos, force, parts))

            payload = [item for item in payload if self._source_enabled(item.get("source", ""))]

            if not parts:
                self.status_updated.emit("Surface obs idle")
                self.observations_updated.emit([])
            else:
                self.status_updated.emit("  |  ".join(parts))
                self.observations_updated.emit(payload)
            self.diagnostics_updated.emit(self._diagnostics_snapshot())
        except Exception as exc:
            log.error("surface fetch failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
        finally:
            rerun = False
            with self._state_lock:
                if self._pending_refresh:
                    self._pending_refresh = False
                    rerun = True
                else:
                    self._running = False
            if rerun:
                threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_station_obs(self, source: Source) -> list[dict]:
        """One station_obs source's current obs (its station list first, when
        it has one and it's due)."""
        stations = None
        if source.stations is not None:
            cached = self._station_cache.get(source.id)
            now = datetime.now(timezone.utc)
            if cached is None or (now - cached[1]).total_seconds() >= source.stations.refresh_min * 60 - 10:
                raw = self._source_get(source, source.stations.url)
                data = self._decode(raw, source.stations.format, f"{source.short} station list",
                                    source.stations.url)
                try:
                    stations = read_stations(data, source.stations, source.missing)
                except ValueError as exc:
                    raise SurfaceFetchError(f"{source.short} station list: {exc}") from exc
                self._station_cache[source.id] = (stations, now)
            else:
                stations = cached[0]
        if source.fetch is not None:
            # a code provider's source: rows already in STORM's fields
            data = source.fetch()
        else:
            raw = self._source_get(source, source.url)
            data = self._decode(raw, source.format, f"{source.short} data", source.url)
        try:
            items = read_station_obs(source, data, stations)
        except ValueError as exc:
            raise SurfaceFetchError(f"{source.short} data: {exc}") from exc
        if not items:
            raise SurfaceFetchError(f"{source.short} data returned no station rows")
        return items

    def _source_get(self, source: Source, url: str) -> bytes:
        found = source.auth(url)
        if found is None:
            return self._http_get(url, ssl_ctx=None, headers={})
        url, headers = found.request(url)
        try:
            return self._http_get(url, ssl_ctx=found.ssl_context(), headers=headers)
        except SurfaceFetchError as exc:
            if found.api_key_param and found.api_key:
                # the key is in the URL; keep it out of the logs and the status line
                raise SurfaceFetchError(str(exc).replace(found.api_key, "***")) from None
            raise

    def _decode(self, raw: bytes, fmt: str, label: str, url: str):
        if fmt == "json":
            return self._json_from_bytes(raw, label, url)
        return decode(raw, fmt)

    def set_asos_enabled(self, enabled: bool):
        self._asos_enabled = enabled
        if enabled:
            if self._asos_bbox is not None:
                # reuse the last drawn bbox immediately
                self._update_timer()
                self.fetch_now()
        else:
            # disable fetching but KEEP the bbox so re-enabling reuses it
            self._update_timer()
            self.fetch_now()

    def fetch_asos_bbox(self, west: float, south: float, east: float, north: float):
        """Store the bbox and kick off the first fetch. The timer handles subsequent refreshes."""
        self._asos_bbox = (west, south, east, north)
        self._update_timer()
        self.fetch_now()

    def clear_asos_bbox(self):
        """Clear the saved ASOS bbox so the next ASOS action asks for a new domain."""
        self._asos_bbox = None
        self._update_timer()

    def _ensure_asos_stations(self) -> dict[str, dict]:
        """Return in-memory station dict, building/loading it if needed."""
        if self._asos_stations is None:
            self._asos_stations = load_asos_station_roster()
        return self._asos_stations

    def _fetch_asos(self) -> list[dict]:
        """Fetch ASOS METARs via IEM currents API using a local station metadata file."""
        west, south, east, north = self._asos_bbox

        stations = self._ensure_asos_stations()

        # filter by bbox
        in_bbox = [
            stid for stid, meta in stations.items()
            if south <= meta["lat"] <= north and west <= meta["lon"] <= east
        ]

        if not in_bbox:
            log.info("ASOS: no stations found in bbox %s", self._asos_bbox)
            return []

        if len(in_bbox) > MAX_ASOS_STATIONS:
            log.warning(
                "ASOS: bbox contains %d stations; capping at %d — draw a smaller box",
                len(in_bbox), MAX_ASOS_STATIONS,
            )
            in_bbox = in_bbox[:MAX_ASOS_STATIONS]

        # batch-fetch from IEM (100 stations per request)
        observations: list[dict] = []
        for i in range(0, len(in_bbox), IEM_CURRENTS_BATCH):
            batch = in_bbox[i : i + IEM_CURRENTS_BATCH]
            observations.extend(self._fetch_iem_batch(batch, stations))

        log.info("ASOS: fetched %d obs for %d in-bbox stations", len(observations), len(in_bbox))
        return observations

    def _fetch_iem_batch(self, stids: list[str], stations: dict[str, dict]) -> list[dict]:
        """Fetch current obs from IEM for a batch of station IDs."""
        params = "&".join(f"station={s}" for s in stids)
        url    = f"{IEM_CURRENTS_URL}?{params}"

        raw  = self._http_get(url)
        data = self._json_from_bytes(raw, "ASOS current observations", url)

        observations: list[dict] = []
        for rec in data.get("data", []):
            stid = (rec.get("station") or "").strip().upper()
            if not stid:
                continue

            meta = stations.get(stid, {})
            lat  = float_or_none(rec.get("lat"), _MISSING)  or meta.get("lat")
            lon  = float_or_none(rec.get("lon"), _MISSING)  or meta.get("lon")
            if lat is None or lon is None:
                continue

            ts = self._parse_iso_utc(rec.get("utc_valid") or rec.get("valid"))

            # iem returns temperature/dewpoint in °F
            tmpf = float_or_none(rec.get("tmpf"), _MISSING)
            dwpf = float_or_none(rec.get("dwpf"), _MISSING)
            temp_c = (tmpf - 32.0) * 5.0 / 9.0 if tmpf is not None else None
            dew_c  = (dwpf - 32.0) * 5.0 / 9.0 if dwpf is not None else None

            # wind speed in knots → m/s
            sknt   = float_or_none(rec.get("sknt"), _MISSING)
            wspd_ms = sknt * 0.514444 if sknt is not None else None

            wdir = float_or_none(rec.get("drct"), _MISSING)

            # prefer mslp (already mb); fall back to altimeter (in-Hg)
            mslp = float_or_none(rec.get("mslp"), _MISSING)
            pres = mslp if (mslp is not None and 870.0 <= mslp <= 1090.0) else None
            if pres is None:
                alti = float_or_none(rec.get("alti"), _MISSING)
                if alti is not None and 27.0 <= alti <= 32.0:
                    pres = alti * 33.8639

            name = meta.get("name") or rec.get("name") or stid

            obs = Observation(
                vehicle_id=f"surface:asos:{stid}",
                lat=lat,
                lon=lon,
                timestamp=ts,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dew_c,
                wind_speed_ms=wspd_ms,
                wind_dir_deg=wdir,
                pressure_mb=pres,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "asos",
                "name": name,
                "obs": obs,
            })

        return observations


    def _http_get(self, url: str, ssl_ctx=None, headers: dict[str, str] | None = None) -> bytes:
        req_headers = dict(self._headers)
        if headers:
            req_headers.update(headers)
        req = Request(url, headers=req_headers)
        try:
            with urlopen(req, timeout=30, context=ssl_ctx) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                body = resp.read()
                if status and status >= 400:
                    raise SurfaceFetchError(f"HTTP {status} from {url}")
                if not body:
                    raise SurfaceFetchError(f"empty response from {url}")
                return body
        except HTTPError as exc:
            preview = self._preview_bytes(exc.read())
            raise SurfaceFetchError(f"HTTP {exc.code} from {url}: {preview}") from exc
        except URLError as exc:
            raise SurfaceFetchError(f"network error from {url}: {exc.reason}") from exc

    def _fetch_part(self, key: str, label: str, fetch_fn, parts: list[str]) -> list[dict]:
        """One source's obs (or its last good ones), with its status and diagnostics."""
        attempt = datetime.now(timezone.utc)
        obs, stale, note = self._fetch_source(label, key, fetch_fn)
        valid = max((i["obs"].timestamp for i in obs), default=None)
        stamp = valid.strftime("%H:%MZ") if valid else "?"
        self._update_source_diag(key, label, attempt, obs, stale, note)
        parts.append(self._format_status_part(label, len(obs), stamp, stale, note))
        return obs

    def _fetch_source(self, label: str, source_key: str, fetch_fn) -> tuple[list[dict], bool, str | None]:
        try:
            observations = fetch_fn()
            self._last_good[source_key] = (list(observations), datetime.now(timezone.utc))
            return observations, False, None
        except Exception as exc:
            log.error("%s surface fetch failed: %s", label, exc, exc_info=True)
            self.error.emit(f"{label}: {exc}")
            cached = self._last_good.get(source_key)
            if cached:
                observations, cached_at = cached
                age_min = max(0, int((datetime.now(timezone.utc) - cached_at).total_seconds() // 60))
                log.warning("%s: using last good surface obs from %d min ago", label, age_min)
                return list(observations), True, f"{age_min}m"
            return [], False, "error"

    @staticmethod
    def _format_status_part(label: str, count: int, stamp: str, stale: bool, note: str | None) -> str:
        if stale:
            return f"{label} {count} {stamp} STALE {note or ''}".rstrip()
        if note == "error":
            return f"{label} error"
        return f"{label} {count} {stamp}"

    def _update_source_diag(
        self,
        source_key: str,
        label: str,
        attempted_at: datetime,
        observations: list[dict],
        stale: bool,
        note: str | None,
    ) -> None:
        valid_time = max((item["obs"].timestamp for item in observations), default=None)
        diag = self._source_diag.get(source_key, {"label": label})
        diag.update({
            "label": label,
            "last_attempt": attempted_at,
            "valid_time": valid_time,
            "count": len(observations),
            "stale": stale,
            "note": note,
        })
        if observations and not stale:
            diag["last_success"] = attempted_at
        self._source_diag[source_key] = diag

    def _diagnostics_snapshot(self) -> dict[str, dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for key, enabled in (
            *self._networks_enabled.items(),
            ("asos", self._asos_enabled and self._asos_bbox is not None),
        ):
            if enabled:
                active[key] = dict(self._source_diag.get(key, {}))
        return active

    def _json_from_bytes(self, raw: bytes, label: str, url: str) -> Any:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            raise SurfaceFetchError(f"{label} returned an empty body from {url}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            preview = self._preview_text(text)
            raise SurfaceFetchError(
                f"{label} returned invalid JSON at char {exc.pos} from {url}: {preview}"
            ) from exc

    @staticmethod
    def _preview_bytes(raw: bytes, limit: int = 180) -> str:
        return SurfaceFetcher._preview_text(raw.decode("utf-8", errors="replace"), limit=limit)

    @staticmethod
    def _preview_text(text: str, limit: int = 180) -> str:
        compact = " ".join(str(text).split())
        if len(compact) > limit:
            compact = compact[:limit] + "..."
        return compact or "<empty>"

    def _source_enabled(self, source: str) -> bool:
        if source == "asos":
            return self._asos_enabled
        return self._networks_enabled.get(source, False)

    @staticmethod
    def _parse_iso_utc(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
