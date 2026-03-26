from __future__ import annotations

import csv
import io
import json
import logging
import math
import threading
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)


OK_CURRENT_URL      = "https://www.mesonet.org/data/public/mesonet/current/current.csv.txt"
KS_STATIONNAMES_URL = "http://mesonet.k-state.edu/rest/stationnames/"
KS_STATIONDATA_URL  = "http://mesonet.k-state.edu/rest/stationdata/"
WTM_SITES_URL       = "https://api.mesonet.ttu.edu/mesoweb/sites/"
WTM_LATEST_URL      = "https://api.mesonet.ttu.edu/mesoweb/public/table/latest/"

_MDF_MISSING  = frozenset({-999.0, -998.0, -997.0, -996.0, -995.0, -994.0})
_CARDINAL_DEG = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}
_KS_UTC_OFFSET = timedelta(hours=6)  # Kansas Mesonet API uses CST (UTC-6) year-round


def _dewpoint_from_rh(temp_c: float, rh_pct: float) -> float:
    """Magnus formula: compute dewpoint (°C) from temperature (°C) and RH (%)."""
    rh_pct = max(1.0, min(100.0, rh_pct))
    a, b   = 17.625, 243.04
    alpha  = math.log(rh_pct / 100.0) + a * temp_c / (b + temp_c)
    return b * alpha / (a - alpha)


class SurfaceFetcher(QObject):
    observations_updated = pyqtSignal(object)
    status_updated = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(300_000)
        self._timer.timeout.connect(self.fetch_now)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._running = False
        self._pending_refresh = False
        self._ok_enabled  = False
        self._wtm_enabled = False
        self._ks_enabled  = False
        self._wtm_meta: dict[int, dict] | None   = None
        self._ks_meta:  dict[str, dict] | None   = None
        self._headers = {
            "User-Agent": "STORM/1.0",
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

    def set_ks_enabled(self, enabled: bool):
        self._ks_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def _update_timer(self):
        if self._ok_enabled or self._wtm_enabled or self._ks_enabled:
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
                parts.append(f"OK {len(ok_obs)}")

            if self._wtm_enabled:
                wtm_obs = self._fetch_source("WTM", self._fetch_wtm)
                payload.extend(wtm_obs)
                parts.append(f"WTM {len(wtm_obs)}")

            if self._ks_enabled:
                ks_obs = self._fetch_source("KS", self._fetch_ks_mesonet)
                payload.extend(ks_obs)
                parts.append(f"KS {len(ks_obs)}")

            payload = [item for item in payload if self._source_enabled(item.get("source", ""))]

            if not parts:
                self.status_updated.emit("Surface obs idle")
                self.observations_updated.emit([])
            else:
                latest_obs_time = max(
                    (item["obs"].timestamp for item in payload),
                    default=datetime.now(timezone.utc),
                )
                stamp = latest_obs_time.strftime("%H:%MZ")
                self.status_updated.emit(f"{'  |  '.join(parts)}  |  OBS {stamp}")
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
        raw = self._http_get(OK_CURRENT_URL).decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)

        now_utc = datetime.now(timezone.utc)
        observations: list[dict] = []

        for row in reader:
            stid = (row.get("STID") or "").strip().lower()
            lat  = self._float_or_none(row.get("LAT"))
            lon  = self._float_or_none(row.get("LON"))
            if not stid or lat is None or lon is None:
                continue

            try:
                obs_time = now_utc.replace(
                    hour=int(row.get("HR", 0)),
                    minute=int(row.get("MI", 0)),
                    second=0, microsecond=0,
                )
            except (ValueError, TypeError):
                obs_time = now_utc

            tair_f = self._mdf_float(row.get("TAIR"))
            tdew_f = self._mdf_float(row.get("TDEW"))
            temp_c = (tair_f - 32) * 5 / 9 if tair_f is not None else None
            dewp_c = (tdew_f - 32) * 5 / 9 if tdew_f is not None else None

            wspd_mph = self._mdf_float(row.get("WSPD"))
            wspd_ms  = wspd_mph * 0.44704 if wspd_mph is not None else None

            wdir_str = (row.get("WDIR") or "").strip().upper()
            wdir_deg = _CARDINAL_DEG.get(wdir_str)

            obs = Observation(
                vehicle_id=f"surface:ok:{stid}",
                lat=lat,
                lon=lon,
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dewp_c,
                wind_speed_ms=wspd_ms,
                wind_dir_deg=wdir_deg,
                pressure_mb=self._mdf_float(row.get("PRES")),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ok",
                "name": (row.get("NAME") or stid.upper()).strip(),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("OK Mesonet current data returned no station rows")

        return observations

    def _fetch_wtm(self) -> list[dict]:
        if self._wtm_meta is None:
            self._wtm_meta = self._fetch_wtm_metadata()

        raw = self._http_get(WTM_LATEST_URL).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        results = data.get("results", [])
        observations: list[dict] = []

        for row in results:
            station_num = row.get("station")
            if station_num is None:
                continue
            meta = (self._wtm_meta or {}).get(int(station_num))
            if not meta:
                continue

            temp_c = self._float_or_none(row.get("temp1p5m"))
            dewpoint_c = self._float_or_none(row.get("dp1p5m"))
            wind_speed_ms = self._float_or_none(row.get("wspd10m"))
            wind_dir_deg = self._float_or_none(row.get("wdir10m"))
            pressure_mb = self._float_or_none(row.get("pres"))
            obs_time = self._parse_iso_utc(row.get("utc"))

            obs = Observation(
                vehicle_id=f"surface:wtm:{meta['mesonet_id'].lower()}",
                lat=float(meta["latitude"]),
                lon=float(meta["longitude"]),
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dewpoint_c,
                wind_speed_ms=wind_speed_ms,
                wind_dir_deg=wind_dir_deg,
                pressure_mb=pressure_mb,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "wtm",
                "name": meta.get("name", row.get("name", meta["mesonet_id"])),
                "obs": obs,
            })

        return observations

    def _fetch_wtm_metadata(self) -> dict[int, dict]:
        raw = self._http_get(WTM_SITES_URL).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        return {
            int(row["station_id"]): row
            for row in data.get("results", [])
            if row.get("station_id") is not None
        }

    def _fetch_ks_mesonet(self) -> list[dict]:
        if self._ks_meta is None:
            self._ks_meta = self._fetch_ks_metadata()

        now_cst = datetime.now(timezone.utc) - _KS_UTC_OFFSET
        t_end   = now_cst.strftime("%Y%m%d%H%M%S")
        t_start = (now_cst - timedelta(minutes=15)).strftime("%Y%m%d%H%M%S")
        url = (
            f"{KS_STATIONDATA_URL}?net=KSRE&int=5min"
            f"&t_start={t_start}&t_end={t_end}"
            f"&vars=TIMESTAMP,STATION,TEMP2MAVG,RELHUM2MAVG,WSPD10MAVG,WDIR10M,PRESSUREAVG"
        )
        raw    = self._http_get(url).decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)

        # Keep only the most recent row per station that has valid (non-M) data.
        # The KS API often returns an incomplete latest interval with all-M values;
        # prefer the most recent row where TEMP2MAVG is present over a newer all-M row.
        latest: dict[str, dict] = {}       # most recent row with valid temp
        latest_any: dict[str, dict] = {}   # most recent row regardless of validity
        for row in reader:
            name = (row.get("STATION") or "").strip()
            if not name:
                continue
            ts_str = (row.get("TIMESTAMP") or "").strip()
            if name not in latest_any or ts_str > latest_any[name].get("TIMESTAMP", ""):
                latest_any[name] = row
            temp_val = (row.get("TEMP2MAVG") or "").strip()
            if temp_val and temp_val != "M":
                if name not in latest or ts_str > latest[name].get("TIMESTAMP", ""):
                    latest[name] = row
        # Fall back to any row for stations that had no valid-temp rows at all
        for name, row in latest_any.items():
            if name not in latest:
                latest[name] = row

        observations: list[dict] = []
        for name, row in latest.items():
            meta = (self._ks_meta or {}).get(name)
            if not meta:
                continue

            temp_c = self._float_m(row.get("TEMP2MAVG"))
            rh_pct = self._float_m(row.get("RELHUM2MAVG"))
            dewp_c = _dewpoint_from_rh(temp_c, rh_pct) if temp_c is not None and rh_pct is not None else None
            wspd_ms  = self._float_m(row.get("WSPD10MAVG"))
            wdir_deg = self._float_m(row.get("WDIR10M"))
            pres_kpa = self._float_m(row.get("PRESSUREAVG"))
            pres_mb  = pres_kpa * 10.0 if pres_kpa is not None else None

            ts_str = (row.get("TIMESTAMP") or "").strip()
            try:
                ts_naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                ks_tz    = timezone(-_KS_UTC_OFFSET)
                obs_time = ts_naive.replace(tzinfo=ks_tz).astimezone(timezone.utc)
            except (ValueError, TypeError):
                obs_time = datetime.now(timezone.utc)

            station_id = f"surface:ks:{name.lower().replace(' ', '_')}"
            obs = Observation(
                vehicle_id=station_id,
                lat=meta["lat"],
                lon=meta["lon"],
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dewp_c,
                wind_speed_ms=wspd_ms,
                wind_dir_deg=wdir_deg,
                pressure_mb=pres_mb,
            )
            observations.append({
                "id": station_id,
                "source": "ks",
                "name": meta.get("display_name", name),
                "obs": obs,
            })

        return observations

    def _fetch_ks_metadata(self) -> dict[str, dict]:
        """Fetch Kansas Mesonet station list; returns {NAME: {lat, lon, display_name}}."""
        raw    = self._http_get(KS_STATIONNAMES_URL).decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)
        meta: dict[str, dict] = {}
        for row in reader:
            name = (row.get("NAME") or "").strip()
            lat  = self._float_or_none(row.get("LATITUDE"))
            lon  = self._float_or_none(row.get("LONGITUDE"))
            if not name or lat is None or lon is None:
                continue
            meta[name] = {
                "lat": lat,
                "lon": lon,
                "display_name": name,
            }
        return meta

    def _http_get(self, url: str) -> bytes:
        req = Request(url, headers=self._headers)
        with self._lock:
            with urlopen(req, timeout=25) as resp:
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
            or (source == "ks"  and self._ks_enabled)
        )

    @staticmethod
    def _float_m(value: str | None) -> float | None:
        """Like _float_or_none but also treats the Kansas Mesonet missing sentinel 'M' as None."""
        if value is None:
            return None
        value = str(value).strip()
        if not value or value == "M":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @classmethod
    def _mdf_float(cls, value: str | None) -> float | None:
        """Like _float_or_none but also treats MDF sentinel values as missing."""
        v = cls._float_or_none(value)
        return None if v in _MDF_MISSING else v

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
