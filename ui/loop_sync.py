"""Shared loop time for live playback (FEATURE_PLAN.md step 2).

The radar loop drives: each frame it shows publishes that frame's time here, and
satellite and vehicles follow. With radar off, the satellite loop drives and only
vehicles follow. ``None`` means live. ARCHIVE has its own TimeController and does
not use this.
"""
from __future__ import annotations

import bisect
import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

RADAR = "radar"
SATELLITE = "satellite"

# how far back a satellite frame may be from the loop time and still be shown
SATELLITE_TOLERANCE_S = {"conus": 600, "meso1": 180, "meso2": 180}
# longest the radar loop waits for CONUS satellite tiles before stepping anyway
TILE_WAIT_S = 1.5


def frame_at(times: list[datetime], t: datetime, tolerance_s: float) -> int | None:
    """Index of the latest time at or before ``t``, if within ``tolerance_s``.

    ``times`` must be sorted ascending.
    """
    i = bisect.bisect_right(times, t) - 1
    if i < 0 or (t - times[i]).total_seconds() > tolerance_s:
        return None
    return i


class LoopSync(QObject):
    """Holds the loop time, who drives it, and whether layers follow it.

    Signals:
        time_changed(object, str) — effective loop time (datetime or None), driver
        vehicles_changed(bool)    — "include vehicles" turned on/off
    """

    time_changed = pyqtSignal(object, str)
    vehicles_changed = pyqtSignal(bool)

    def __init__(self, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._clock = clock
        self._enabled = True
        self._vehicles = False
        self._time: datetime | None = None
        self._source = RADAR
        self._tiles_pending_since: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def vehicles(self) -> bool:
        """True when vehicles follow the loop (needs sync on)."""
        return self._enabled and self._vehicles

    @property
    def source(self) -> str:
        return self._source

    @property
    def loop_time(self) -> datetime | None:
        """Effective loop time: None while live or with sync off."""
        return self._time if self._enabled else None

    def set_time(self, t: datetime | None, source: str = RADAR) -> None:
        if t == self._time and source == self._source:
            return
        before = self.loop_time
        self._time = t
        self._source = source
        if self.loop_time != before or (self._enabled and t is not None):
            self.time_changed.emit(self.loop_time, self._source)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        before_vehicles = self.vehicles
        self._enabled = enabled
        self._tiles_pending_since = None
        self.time_changed.emit(self.loop_time, self._source)
        if self.vehicles != before_vehicles:
            self.vehicles_changed.emit(self.vehicles)

    def set_vehicles(self, enabled: bool) -> None:
        before = self.vehicles
        self._vehicles = bool(enabled)
        if self.vehicles != before:
            self.vehicles_changed.emit(self.vehicles)

    # CONUS satellite frames are WMS tiles that load after the time changes;
    # the radar loop holds its next step until they land or TILE_WAIT_S passes.
    def mark_tiles_pending(self) -> None:
        self._tiles_pending_since = self._clock()

    def mark_tiles_idle(self) -> None:
        self._tiles_pending_since = None

    def ready_to_step(self) -> bool:
        if self._tiles_pending_since is None:
            return True
        if self._clock() - self._tiles_pending_since >= TILE_WAIT_S:
            self._tiles_pending_since = None
            return True
        return False
