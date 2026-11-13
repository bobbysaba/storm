"""A vehicle's measured fixes, looked up by time: the actual fix nearest a
moment, within a minute, never an interpolated or whole-day position. Used
to place and orient the lidar truck's scans (its lidar files carry no
position, and some no heading)."""
from __future__ import annotations

from math import isfinite

import numpy as np

MAX_GAP_S = 60.0


class PositionTrack:
    """Observations (anything with timestamp / lat / lon, and heading_deg
    when the compass was recorded), sorted, with bad fixes left out."""

    def __init__(self, observations):
        self.observations = sorted(
            (o for o in observations
             if o.lat is not None and o.lon is not None and isfinite(o.lat) and isfinite(o.lon)
             and -90 <= o.lat <= 90 and -180 <= o.lon <= 180 and (o.lat, o.lon) != (0, 0)),
            key=lambda o: o.timestamp,
        )
        self.times = np.array([o.timestamp.timestamp() for o in self.observations], dtype=float)
        self.lat = np.array([o.lat for o in self.observations], dtype=float)
        self.lon = np.array([o.lon for o in self.observations], dtype=float)
        compass = [(t, h) for t, o in zip(self.times, self.observations)
                   if (h := getattr(o, "heading_deg", None)) is not None and isfinite(h)]
        self.compass_times = np.array([t for t, _ in compass], dtype=float)
        self.compass = np.array([h for _, h in compass], dtype=float)

    def __len__(self) -> int:
        return len(self.observations)

    def nearest(self, time, max_gap_seconds: float = MAX_GAP_S):
        """The fix nearest `time` (a datetime or epoch seconds) within
        max_gap_seconds, else None; a tie takes the older fix."""
        epoch = time.timestamp() if hasattr(time, "timestamp") else float(time)
        i = int(_nearest(self.times, np.array([epoch]), max_gap_seconds)[0])
        return self.observations[i] if i >= 0 else None

    def positions_at(self, epochs, max_gap_seconds: float = MAX_GAP_S):
        """(lat, lon, fix epoch) arrays for many times at once; NaN where no
        fix is within max_gap_seconds."""
        i = _nearest(self.times, np.asarray(epochs, dtype=float), max_gap_seconds)
        found = i >= 0
        lat, lon, when = (np.full(i.size, np.nan) for _ in range(3))
        lat[found], lon[found], when[found] = self.lat[i[found]], self.lon[i[found]], self.times[i[found]]
        return lat, lon, when

    def headings_at(self, epochs, max_gap_seconds: float = MAX_GAP_S):
        """The compass heading nearest each time within max_gap_seconds; NaN
        where there's none."""
        i = _nearest(self.compass_times, np.asarray(epochs, dtype=float), max_gap_seconds)
        out = np.full(i.size, np.nan)
        out[i >= 0] = self.compass[i[i >= 0]]
        return out


def _nearest(times: np.ndarray, epochs: np.ndarray, max_gap: float) -> np.ndarray:
    """Index into sorted `times` of the value nearest each epoch, or -1 when
    none is within max_gap (or the epoch isn't finite)."""
    out = np.full(epochs.size, -1, dtype=int)
    if not times.size:
        return out
    right = np.clip(np.searchsorted(times, epochs, side="left"), 0, times.size - 1)
    left = np.clip(right - 1, 0, times.size - 1)
    gap_left, gap_right = np.abs(epochs - times[left]), np.abs(times[right] - epochs)
    best = np.where(gap_left <= gap_right, left, right)
    gap = np.minimum(gap_left, gap_right)
    ok = np.isfinite(epochs) & (gap <= max_gap)
    out[ok] = best[ok]
    return out
