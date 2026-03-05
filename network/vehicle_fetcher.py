# network/vehicle_fetcher.py
# Polls a server-hosted vehicles.json endpoint and emits one Observation
# per vehicle entry so the rest of the app can treat it like any other
# data source (file watcher, GPS, etc.).
#
# Expected JSON format (dict keyed by vehicle_id, latest obs per vehicle):
#
#   {
#     "WX1": {
#       "vehicle_id": "WX1",
#       "lat": 33.19, "lon": -102.27,
#       "gps_date": "050625", "gps_time": "175228",   # DDMMYY / HHMMSS
#       "wspd": 2.5, "wdir": 108.0,
#       "t_fast": 23.2, "dewpoint": 19.9, "pressure": 900.85
#     },
#     "WX2": { ... }
#   }
#
# Met fields are optional — vehicles without a surface obs logger omit them.

import json
import logging
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtCore import QUrl

from core.observation import Observation

log = logging.getLogger(__name__)


class VehicleFetcher(QObject):
    """
    Periodically GETs a vehicles.json URL and emits obs_ready for each
    vehicle entry found.  Uses Qt's async QNetworkAccessManager so the
    fetch never blocks the UI thread.

    Usage:
        fetcher = VehicleFetcher(parent=self)
        fetcher.obs_ready.connect(main_window.update_vehicle_obs)
        fetcher.start("https://yourserver.com/vehicles.json", interval_s=15)
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam   = QNetworkAccessManager(self)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fetch)
        self._url   = ""

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, url: str, interval_s: int = 15):
        if not url:
            log.info("VehicleFetcher: no URL configured — fetcher disabled")
            return
        self._url = url
        self._timer.start(interval_s * 1000)
        self._fetch()   # immediate first fetch on startup
        log.info("VehicleFetcher: polling %s every %ds", url, interval_s)

    def stop(self):
        self._timer.stop()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _fetch(self):
        req = QNetworkRequest(QUrl(self._url))
        req.setRawHeader(b"Accept", b"application/json")
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply):
        try:
            status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
            if status != 200:
                log.warning("VehicleFetcher: HTTP %s from %s", status, self._url)
                return

            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
            data = json.loads(raw)

            if not isinstance(data, dict):
                log.warning("VehicleFetcher: expected JSON object, got %s", type(data))
                return

            emitted = 0
            for vid, entry in data.items():
                obs = _parse_entry(vid, entry)
                if obs is not None:
                    self.obs_ready.emit(obs)
                    emitted += 1

            log.debug("VehicleFetcher: emitted %d obs from %s", emitted, self._url)

        except json.JSONDecodeError as e:
            log.warning("VehicleFetcher: JSON parse error: %s", e)
        except Exception as e:
            log.warning("VehicleFetcher: unexpected error: %s", e)
        finally:
            reply.deleteLater()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_entry(vid: str, entry: dict) -> Observation | None:
    """Parse one vehicle dict from vehicles.json into an Observation."""
    try:
        lat = float(entry["lat"])
        lon = float(entry["lon"])
    except (KeyError, ValueError, TypeError):
        log.debug("VehicleFetcher: skipping malformed entry: %s", entry)
        return None

    return Observation(
        vehicle_id=vid,
        lat=lat,
        lon=lon,
        timestamp=_parse_timestamp(
            entry.get("gps_date", ""),
            entry.get("gps_time", ""),
        ),
        wind_speed_ms=_float_or_none(entry.get("wspd")),
        wind_dir_deg=_float_or_none(entry.get("wdir")),
        temperature_c=_float_or_none(entry.get("t_fast")),
        dewpoint_c=_float_or_none(entry.get("dewpoint")),
        pressure_mb=_float_or_none(entry.get("pressure")),
    )


def _parse_timestamp(gps_date: str, gps_time: str) -> datetime:
    """Convert DDMMYY + HHMMSS strings back to a UTC datetime."""
    date_s = (gps_date or "").strip()
    time_s = (gps_time or "").strip()
    if date_s and time_s:
        try:
            return datetime.strptime(date_s + time_s, "%d%m%y%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
