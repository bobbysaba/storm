from __future__ import annotations

import csv
import io
import json
import logging
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

OK_THREDDS_URL  = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/ok_mesonet.json"
OK_META_URL     = "https://www.mesonet.org/data/public/mesonet/current/current.csv.txt"
WTM_THREDDS_URL = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/wtx_mesonet.json"
WTM_SITES_URL   = "https://api.mesonet.ttu.edu/mesoweb/sites/"



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
        self._ok_meta:  dict[str, dict] | None = None
        self._wtm_meta: dict[str, dict] | None = None
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
        if self._ok_enabled or self._wtm_enabled:
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

    def _http_get(self, url: str, ssl_ctx=None) -> bytes:
        req = Request(url, headers=self._headers)
        with self._lock:
            with urlopen(req, timeout=25, context=ssl_ctx) as resp:
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
