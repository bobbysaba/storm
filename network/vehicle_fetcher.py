# network/vehicle_fetcher.py
# Polls a server-hosted vehicles.json endpoint and emits one Observation
# per vehicle entry so the rest of the app can treat it like any other
# data source (file watcher, GPS, etc.).

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)


class VehicleFetcher(QObject):
    """
    Periodically GETs a vehicles.json URL and emits obs_ready for each
    vehicle entry found.

    Uses Python's urllib in a worker thread rather than QtNetwork to avoid
    platform-specific SSL/network crashes seen on some Windows setups.
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fetch)
        self._url = ""
        self._interval_s = 15
        self._inflight_lock = threading.Lock()
        self._inflight = False

    # Public API

    def start(self, url: str, interval_s: int = 15):
        if not url:
            log.info("VehicleFetcher: no URL configured - fetcher disabled")
            return
        self._url = url
        self._interval_s = max(3, int(interval_s))
        self._timer.start(self._interval_s * 1000)
        # Avoid concurrent TLS startup races with other network clients.
        QTimer.singleShot(2500, self._fetch)
        log.info("VehicleFetcher: polling %s every %ds", url, self._interval_s)

    def stop(self):
        self._timer.stop()

    # Internal

    def _fetch(self):
        with self._inflight_lock:
            if self._inflight:
                return
            self._inflight = True

        t = threading.Thread(target=self._fetch_worker, daemon=True)
        t.start()

    def _fetch_worker(self):
        try:
            req = urllib.request.Request(
                self._url,
                headers={"Accept": "application/json", "User-Agent": "storm/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    log.warning("VehicleFetcher: HTTP %s from %s", status, self._url)
                    return
                raw = resp.read().decode("utf-8", errors="replace")

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
            log.warning("VehicleFetcher: fetch error: %s", e)
        finally:
            with self._inflight_lock:
                self._inflight = False


# Helpers

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
