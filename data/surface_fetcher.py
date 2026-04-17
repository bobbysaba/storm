from __future__ import annotations

import csv
import io
import json
import logging
import pathlib
import ssl
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)

# NSSL THREDDS uses a cert that Python's default SSL context rejects
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

OK_THREDDS_URL  = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/data/mesonet/ok_mesonet.json"
OK_META_URL     = "https://www.mesonet.org/data/public/mesonet/current/current.csv.txt"
WTM_THREDDS_URL = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/data/mesonet/wtx_mesonet.json"
WTM_SITES_URL   = "https://api.mesonet.ttu.edu/mesoweb/sites/"

# IEM endpoints for ASOS
IEM_METAR_GEOJSON = "https://mesonet.agron.iastate.edu/geojson/metar.geojson"
IEM_CURRENTS_URL  = "https://mesonet.agron.iastate.edu/api/1/currents.json"
IEM_CURRENTS_BATCH = 100   # stations per IEM request
MAX_ASOS_STATIONS  = 400   # cap to keep map rendering fast

_ASOS_STATIONS_FILE = pathlib.Path(__file__).parent / "asos_stations.json"


class SurfaceFetcher(QObject):
    observations_updated = pyqtSignal(object)
    status_updated = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(300_000)
        self._timer.timeout.connect(self.fetch_now)
        self._state_lock = threading.Lock()
        self._running = False
        self._pending_refresh = False
        self._ok_enabled  = False
        self._wtm_enabled = False
        self._asos_enabled = False
        self._ok_meta:  dict[str, dict] | None = None
        self._wtm_meta: dict[str, dict] | None = None
        self._asos_stations: dict[str, dict] | None = None   # stid → {lat, lon, name}
        self._asos_bbox: tuple[float, float, float, float] | None = None
        self._headers = {
            "User-Agent": "Mozilla/5.0 STORM/1.0",
            "Accept": "application/json, text/html, application/xml, text/xml",
        }

    def start(self):
        self._update_timer()

    def stop(self):
        self._timer.stop()

    def set_ok_enabled(self, enabled: bool):
        self._ok_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_wtm_enabled(self, enabled: bool):
        self._wtm_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def _update_timer(self):
        if self._ok_enabled or self._wtm_enabled or (self._asos_enabled and self._asos_bbox is not None):
            self._timer.start()
        else:
            self._timer.stop()

    def fetch_now(self):
        with self._state_lock:
            if self._running:
                self._pending_refresh = True
                return
            self._running = True
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            payload: list[dict] = []
            parts: list[str] = []

            if self._ok_enabled:
                ok_obs = self._fetch_source("OK", self._fetch_ok_mesonet)
                payload.extend(ok_obs)
                ok_time = max((i["obs"].timestamp for i in ok_obs), default=None)
                stamp = ok_time.strftime("%H:%MZ") if ok_time else "?"
                parts.append(f"OK {len(ok_obs)} {stamp}")

            if self._wtm_enabled:
                wtm_obs = self._fetch_source("WTM", self._fetch_wtm)
                payload.extend(wtm_obs)
                wtm_time = max((i["obs"].timestamp for i in wtm_obs), default=None)
                stamp = wtm_time.strftime("%H:%MZ") if wtm_time else "?"
                parts.append(f"WTM {len(wtm_obs)} {stamp}")

            if self._asos_enabled and self._asos_bbox is not None:
                asos_obs = self._fetch_source("ASOS", self._fetch_asos)
                payload.extend(asos_obs)
                asos_time = max((i["obs"].timestamp for i in asos_obs), default=None)
                stamp = asos_time.strftime("%H:%MZ") if asos_time else "?"
                parts.append(f"ASOS {len(asos_obs)} {stamp}")

            payload = [item for item in payload if self._source_enabled(item.get("source", ""))]

            if not parts:
                self.status_updated.emit("Surface obs idle")
                self.observations_updated.emit([])
            else:
                self.status_updated.emit("  |  ".join(parts))
                self.observations_updated.emit(payload)
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

    def _fetch_ok_mesonet(self) -> list[dict]:
        if self._ok_meta is None:
            self._ok_meta = self._fetch_ok_metadata()

        raw  = self._http_get(OK_THREDDS_URL, ssl_ctx=_SSL_CTX).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        obs_time = self._parse_iso_utc(data.get("time"))
        obs_data = data.get("data", {})

        # Build per-station dict: stid -> {var: value}
        all_stids: set[str] = set()
        for var_vals in obs_data.values():
            all_stids.update(var_vals.keys())

        observations: list[dict] = []
        for stid in all_stids:
            meta = (self._ok_meta or {}).get(stid)
            if not meta:
                continue
            obs = Observation(
                vehicle_id=f"surface:ok:{stid}",
                lat=meta["lat"],
                lon=meta["lon"],
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=self._float_or_none(obs_data.get("tair", {}).get(stid)),
                dewpoint_c=self._float_or_none(obs_data.get("tdew", {}).get(stid)),
                wind_speed_ms=self._float_or_none(obs_data.get("wspd", {}).get(stid)),
                wind_dir_deg=self._float_or_none(obs_data.get("wdir", {}).get(stid)),
                pressure_mb=self._float_or_none(obs_data.get("pres", {}).get(stid)),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ok",
                "name": meta.get("name", stid.upper()),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("OK Mesonet THREDDS data returned no station rows")

        return observations

    def _fetch_ok_metadata(self) -> dict[str, dict]:
        """Fetch OK Mesonet station lat/lon/name from the public CSV."""
        raw    = self._http_get(OK_META_URL).decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)
        meta: dict[str, dict] = {}
        for row in reader:
            stid = (row.get("STID") or "").strip().lower()
            lat  = self._float_or_none(row.get("LAT"))
            lon  = self._float_or_none(row.get("LON"))
            if not stid or lat is None or lon is None:
                continue
            meta[stid] = {
                "lat": lat,
                "lon": lon,
                "name": (row.get("NAME") or stid.upper()).strip(),
            }
        return meta

    def _fetch_wtm(self) -> list[dict]:
        if self._wtm_meta is None:
            self._wtm_meta = self._fetch_wtm_metadata()

        raw  = self._http_get(WTM_THREDDS_URL, ssl_ctx=_SSL_CTX).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        observations: list[dict] = []

        for row in data.get("results", []):
            mid = (row.get("mid") or "").strip().lower()
            if not mid:
                continue
            meta = (self._wtm_meta or {}).get(mid)
            if not meta:
                continue
            obs = Observation(
                vehicle_id=f"surface:wtm:{mid}",
                lat=meta["lat"],
                lon=meta["lon"],
                timestamp=self._parse_iso_utc(row.get("utc")),
                icon_type="mesonet",
                temperature_c=self._float_or_none(row.get("temp1p5m")),
                dewpoint_c=self._float_or_none(row.get("dp1p5m")),
                wind_speed_ms=self._float_or_none(row.get("wspd10m")),
                wind_dir_deg=self._float_or_none(row.get("wdir10m")),
                pressure_mb=self._float_or_none(row.get("pres")),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "wtm",
                "name": meta.get("name", row.get("name", mid.upper())),
                "obs": obs,
            })

        return observations

    def _fetch_wtm_metadata(self) -> dict[str, dict]:
        raw  = self._http_get(WTM_SITES_URL).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        return {
            row["mesonet_id"].lower(): {
                "lat": float(row["latitude"]),
                "lon": float(row["longitude"]),
                "name": row.get("name", row["mesonet_id"]),
            }
            for row in data.get("results", [])
            if row.get("mesonet_id") and row.get("latitude") and row.get("longitude")
        }

    # ── ASOS / IEM ────────────────────────────────────────────────────────────

    def set_asos_enabled(self, enabled: bool):
        self._asos_enabled = enabled
        if enabled:
            if self._asos_bbox is not None:
                # Reuse the last drawn bbox immediately
                self._update_timer()
                self.fetch_now()
            else:
                self.status_updated.emit("ASOS: click and drag a box on the map to select domain")
        else:
            # Disable fetching but KEEP the bbox so re-enabling reuses it
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
        if self._asos_stations is not None:
            return self._asos_stations

        if _ASOS_STATIONS_FILE.exists():
            try:
                self._asos_stations = json.loads(_ASOS_STATIONS_FILE.read_text("utf-8"))
                log.info("ASOS: loaded %d stations from %s", len(self._asos_stations), _ASOS_STATIONS_FILE)
                return self._asos_stations
            except Exception as exc:
                log.warning("ASOS: failed to read station file, re-fetching: %s", exc)

        log.info("ASOS: fetching station metadata from IEM metar.geojson …")
        raw = self._http_get(IEM_METAR_GEOJSON).decode("utf-8", errors="ignore")
        fc  = json.loads(raw)

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

        try:
            _ASOS_STATIONS_FILE.write_text(json.dumps(stations, separators=(",", ":")), "utf-8")
            log.info("ASOS: saved %d stations to %s", len(stations), _ASOS_STATIONS_FILE)
        except Exception as exc:
            log.warning("ASOS: could not save station file: %s", exc)

        self._asos_stations = stations
        return stations

    def _fetch_asos(self) -> list[dict]:
        """Fetch ASOS METARs via IEM currents API using a local station metadata file."""
        west, south, east, north = self._asos_bbox

        stations = self._ensure_asos_stations()

        # Filter by bbox
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

        # Batch-fetch from IEM (100 stations per request)
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

        raw  = self._http_get(url).decode("utf-8", errors="ignore")
        data = json.loads(raw)

        observations: list[dict] = []
        for rec in data.get("data", []):
            stid = (rec.get("station") or "").strip().upper()
            if not stid:
                continue

            meta = stations.get(stid, {})
            lat  = self._float_or_none(rec.get("lat"))  or meta.get("lat")
            lon  = self._float_or_none(rec.get("lon"))  or meta.get("lon")
            if lat is None or lon is None:
                continue

            ts = self._parse_iso_utc(rec.get("valid"))

            # IEM returns temperature/dewpoint in °F
            tmpf = self._float_or_none(rec.get("tmpf"))
            dwpf = self._float_or_none(rec.get("dwpf"))
            temp_c = (tmpf - 32.0) * 5.0 / 9.0 if tmpf is not None else None
            dew_c  = (dwpf - 32.0) * 5.0 / 9.0 if dwpf is not None else None

            # Wind speed in knots → m/s
            sknt   = self._float_or_none(rec.get("sknt"))
            wspd_ms = sknt * 0.514444 if sknt is not None else None

            wdir = self._float_or_none(rec.get("drct"))

            # Prefer mslp (already mb); fall back to altimeter (in-Hg)
            mslp = self._float_or_none(rec.get("mslp"))
            pres = mslp if (mslp is not None and 870.0 <= mslp <= 1090.0) else None
            if pres is None:
                alti = self._float_or_none(rec.get("alti"))
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

    # ── Shared HTTP ───────────────────────────────────────────────────────────

    def _http_get(self, url: str, ssl_ctx=None) -> bytes:
        req = Request(url, headers=self._headers)
        with urlopen(req, timeout=30, context=ssl_ctx) as resp:
            return resp.read()

    def _fetch_source(self, label: str, fetch_fn) -> list[dict]:
        try:
            return fetch_fn()
        except Exception as exc:
            log.error("%s surface fetch failed: %s", label, exc, exc_info=True)
            self.error.emit(f"{label}: {exc}")
            return []

    def _source_enabled(self, source: str) -> bool:
        return (
            (source == "ok"  and self._ok_enabled)
            or (source == "wtm" and self._wtm_enabled)
            or (source == "asos" and self._asos_enabled)
        )

    @staticmethod
    def _float_or_none(value: str | None) -> float | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value or value == "--":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_iso_utc(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
