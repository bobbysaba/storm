from __future__ import annotations

import gzip
import json
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)


SURFACE_BBOX = (-116.0, 28.0, -82.0, 49.0)
OK_META_URL = "https://api.prod.mesonet.org/index.php/meta/sites/status/active/vars/stid,name,lat,lon,wfo"
OK_EXPORT_URL = (
    "https://api.prod.mesonet.org/index.php/export/mesonet_data_files"
    "?date={stamp}&format=html&type=mdf&units=metric"
)
WTM_SITES_URL = "https://api.mesonet.ttu.edu/mesoweb/sites/"
WTM_LATEST_URL = "https://api.mesonet.ttu.edu/mesoweb/public/table/latest/"
METAR_CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.xml.gz"


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
        self._ok_enabled = False
        self._wtm_enabled = False
        self._metar_enabled = False
        self._ok_meta: dict[str, dict] | None = None
        self._wtm_meta: dict[int, dict] | None = None
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

    def set_metar_enabled(self, enabled: bool):
        self._metar_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def _update_timer(self):
        if self._ok_enabled or self._wtm_enabled or self._metar_enabled:
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

            if self._metar_enabled:
                metar_obs = self._fetch_source("METAR", self._fetch_metar)
                payload.extend(metar_obs)
                parts.append(f"METAR {len(metar_obs)}")

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
        if self._ok_meta is None:
            self._ok_meta = self._fetch_ok_metadata()

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        rounded = now - timedelta(minutes=now.minute % 5)
        last_exc = None
        html = ""
        rows: list[str] = []
        selected_time = rounded

        for attempt in range(3):
            candidate_time = rounded - timedelta(minutes=5 * attempt)
            stamp = candidate_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                candidate_html = self._http_get(OK_EXPORT_URL.format(stamp=stamp)).decode("utf-8", errors="ignore")
                candidate_rows = re.findall(
                    r"<tr[^>]*>\s*<th[^>]*scope=[\"']row[\"'][^>]*>.*?</tr>",
                    candidate_html,
                    flags=re.S | re.I,
                )
                if candidate_rows:
                    html = candidate_html
                    rows = candidate_rows
                    selected_time = candidate_time
                    break
            except Exception as exc:
                last_exc = exc

        if not html:
            raise last_exc or RuntimeError("No Oklahoma Mesonet data returned")

        observations: list[dict] = []

        for row in rows:
            stid_match = re.search(r"<th[^>]*scope=[\"']row[\"'][^>]*>(.*?)</th>", row, flags=re.S | re.I)
            stid = self._clean_cell(stid_match.group(1)).strip().lower() if stid_match else ""
            cells = [self._clean_cell(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
            if len(cells) < 13 or not stid:
                continue
            meta = (self._ok_meta or {}).get(stid)
            if not meta:
                continue

            rh = self._float_or_none(cells[2])
            temp_c = self._float_or_none(cells[3])
            wspd_ms = self._float_or_none(cells[4])
            wdir = self._float_or_none(cells[6])
            pressure_mb = self._float_or_none(cells[10])
            obs_time = self._parse_hhmm_utc(cells[1], selected_time)

            dewpoint_c = self._dewpoint_from_temp_rh(temp_c, rh) if temp_c is not None and rh is not None else None

            obs = Observation(
                vehicle_id=f"surface:ok:{stid}",
                lat=float(meta["lat"]),
                lon=float(meta["lon"]),
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dewpoint_c,
                wind_speed_ms=wspd_ms,
                wind_dir_deg=wdir,
                pressure_mb=pressure_mb,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ok",
                "name": meta.get("name", stid.upper()),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("Oklahoma Mesonet export returned no station rows")

        return observations

    def _fetch_ok_metadata(self) -> dict[str, dict]:
        raw = self._http_get(OK_META_URL).decode("utf-8", errors="ignore")
        data = json.loads(raw)
        return {k.lower(): v for k, v in data.get("response", {}).items()}

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

    def _fetch_metar(self) -> list[dict]:
        raw = self._http_get(METAR_CACHE_URL)
        xml_text = gzip.decompress(raw)
        root = ET.fromstring(xml_text)
        west, south, east, north = SURFACE_BBOX
        observations: list[dict] = []

        for item in root.findall(".//METAR"):
            lat = self._float_or_none(item.findtext("latitude"))
            lon = self._float_or_none(item.findtext("longitude"))
            if lat is None or lon is None:
                continue
            if not (west <= lon <= east and south <= lat <= north):
                continue

            station_id = (item.findtext("station_id") or "").strip().upper()
            if not station_id:
                continue

            temp_c = self._float_or_none(item.findtext("temp_c"))
            dewpoint_c = self._float_or_none(item.findtext("dewpoint_c"))
            wind_speed_kt = self._float_or_none(item.findtext("wind_speed_kt"))
            wind_dir = self._float_or_none(item.findtext("wind_dir_degrees"))
            altim_in_hg = self._float_or_none(item.findtext("altim_in_hg"))
            station_name = (item.findtext("site") or item.findtext("station_id") or station_id).strip()
            obs_time = self._parse_iso_utc(item.findtext("observation_time"))

            obs = Observation(
                vehicle_id=f"surface:metar:{station_id}",
                lat=lat,
                lon=lon,
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dewpoint_c,
                wind_speed_ms=wind_speed_kt * 0.514444 if wind_speed_kt is not None else None,
                wind_dir_deg=wind_dir,
                pressure_mb=altim_in_hg * 33.8639 if altim_in_hg is not None else None,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "metar",
                "name": station_name,
                "obs": obs,
            })

        return observations

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
            (source == "ok" and self._ok_enabled)
            or (source == "wtm" and self._wtm_enabled)
            or (source == "metar" and self._metar_enabled)
        )

    @staticmethod
    def _clean_cell(value: str) -> str:
        value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
        value = re.sub(r"<[^>]+>", "", value)
        return unescape(value).strip()

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

    @staticmethod
    def _parse_hhmm_utc(value: str | None, base_time: datetime) -> datetime:
        if not value:
            return base_time
        try:
            hour_str, minute_str = str(value).strip().split(":", 1)
            return base_time.replace(
                hour=int(hour_str),
                minute=int(minute_str),
                second=0,
                microsecond=0,
            )
        except (ValueError, TypeError):
            return base_time

    @staticmethod
    def _dewpoint_from_temp_rh(temp_c: float, rh: float) -> float | None:
        if rh <= 0 or rh > 100:
            return None
        a = 17.625
        b = 243.04
        gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
        return (b * gamma) / (a - gamma)
