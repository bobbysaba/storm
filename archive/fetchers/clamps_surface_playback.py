"""CLAMPS trailer surface observations replayed on the archive clock.

Loaded by date, whether or not the trailers were on MQTT that day. CLAMPS
runs continuously, so the whole session span is read: its UTC day and the
next morning to the cap (archive/session.py).
"""
from __future__ import annotations

import threading
from bisect import bisect_right
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from core.qt_signals import emit_alive

_STALE_SECONDS = 300    # a trailer with no report for 5 minutes leaves the map


class ClampsSurfacePlayback(QObject):
    loaded = pyqtSignal(object)    # list[Observation]
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = threading.Event()
        self.rows: dict[str, list] = {}
        self._times: dict[str, list[datetime]] = {}

    def load(self, session_time: datetime) -> None:
        threading.Thread(target=self._load, args=(session_time,), daemon=True,
                         name="clamps-surface").start()

    def _load(self, session_time: datetime) -> None:
        from archive.fetchers.clamps_surface_archive_fetcher import fetch_clamps_surface_observations
        from archive.session import session_bounds, session_days
        try:
            start, cap = session_bounds(session_time)
            rows = [obs for day in session_days(session_time)
                    for obs in fetch_clamps_surface_observations(day)
                    if start <= obs.timestamp <= cap]
        except Exception as exc:  # noqa: BLE001
            if not self._closed.is_set():
                emit_alive(self, "error", str(exc))
            return
        if not self._closed.is_set():
            emit_alive(self, "loaded", rows)

    def install(self, rows) -> None:
        """Take a load's rows (on the GUI thread)."""
        self.rows = {}
        for obs in rows:
            obs.icon_type = "lidar"
            self.rows.setdefault(obs.vehicle_id, []).append(obs)
        for observations in self.rows.values():
            observations.sort(key=lambda obs: obs.timestamp)
        self._times = {key: [obs.timestamp for obs in observations]
                       for key, observations in self.rows.items()}

    def at(self, key: str, when: datetime):
        """The trailer's latest report at `when`, or None before its first
        or once it's 5 minutes old."""
        idx = bisect_right(self._times.get(key, []), when) - 1
        if idx < 0:
            return None
        obs = self.rows[key][idx]
        return obs if (when - obs.timestamp).total_seconds() <= _STALE_SECONDS else None

    def history(self, key: str, when: datetime) -> list:
        return self.rows.get(key, [])[:bisect_right(self._times.get(key, []), when)]

    def shutdown(self) -> None:
        self._closed.set()
