# data/obs_history_store.py
# Thread-safe rolling observation buffer.
# Keeps the last OBS_WINDOW_MINUTES of data per vehicle.
# Used by ui/history_widget.py for time-series plots.

import threading
from collections import deque
from datetime import datetime, timezone, timedelta

from core.observation import Observation

OBS_WINDOW_MINUTES = 10


class ObsHistoryStore:
    """
    Rolling per-vehicle observation buffer (default: last 10 minutes).

    Thread-safe: add() may be called from the GPS or file-watcher thread;
    get() is typically called from the main/UI thread.
    """

    def __init__(self, window_minutes: int = OBS_WINDOW_MINUTES):
        self._window = timedelta(minutes=window_minutes)
        self._store: dict[str, deque[Observation]] = {}
        self._lock = threading.Lock()

    # ── Write ──────────────────────────────────────────────────────────────────

    def add(self, obs: Observation) -> None:
        """Append an observation and trim anything older than the window."""
        cutoff = datetime.now(timezone.utc) - self._window
        with self._lock:
            buf = self._store.setdefault(obs.vehicle_id, deque())
            buf.append(obs)
            while buf and buf[0].timestamp < cutoff:
                buf.popleft()

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, vehicle_id: str) -> list[Observation]:
        """Return a copy of the obs list for a vehicle, oldest first."""
        with self._lock:
            return list(self._store.get(vehicle_id, []))

    def vehicle_ids(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    # ── Maintenance ────────────────────────────────────────────────────────────

    def clear(self, vehicle_id: str | None = None) -> None:
        with self._lock:
            if vehicle_id:
                self._store.pop(vehicle_id, None)
            else:
                self._store.clear()
