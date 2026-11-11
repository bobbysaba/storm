import bisect


class Vehicle:
    # create a new vehicle object
    def __init__(self, id, lat, lon, latest_obs=None, icon_type="car"):
        # assign id
        self.id = id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign latest observation
        self.latest_obs = latest_obs
        # assign icon type (car, drone, mesonet, lidar, radar)
        self.icon_type = icon_type
        # recent positions, so the live loop can show where it was
        self.track = PositionTrack()


class PositionTrack:
    """Recent (time, lat, lon) fixes for one vehicle, in memory only.

    Fixes closer than ``min_spacing_s`` to the previous kept one replace it, so
    the newest fix is always kept. Fixes older than ``keep_s`` behind the newest
    are dropped. Times are UTC epoch seconds.
    """

    def __init__(self, keep_s: float = 90 * 60, min_spacing_s: float = 15.0,
                 max_gap_s: float = 120.0):
        self.keep_s = keep_s
        self.min_spacing_s = min_spacing_s
        self.max_gap_s = max_gap_s
        self._t: list[float] = []
        self._pos: list[tuple[float, float]] = []

    def __len__(self) -> int:
        return len(self._t)

    def append(self, t: float, lat: float, lon: float) -> None:
        if lat is None or lon is None:
            return
        if self._t and t < self._t[-1]:
            return   # out of order (replayed or late MQTT message)
        if len(self._t) >= 2 and t - self._t[-2] < self.min_spacing_s:
            self._t[-1] = t
            self._pos[-1] = (lat, lon)
        else:
            self._t.append(t)
            self._pos.append((lat, lon))
        cutoff = t - self.keep_s
        drop = 0
        while drop < len(self._t) and self._t[drop] < cutoff:
            drop += 1
        if drop:
            del self._t[:drop]
            del self._pos[:drop]

    def position_at(self, t: float) -> tuple[float, float] | None:
        """Position at ``t``: None before the first fix, the last fix after the
        newest, interpolated between fixes, and held across gaps over max_gap_s."""
        if not self._t or t < self._t[0]:
            return None
        i = bisect.bisect_right(self._t, t) - 1
        if i >= len(self._t) - 1:
            return self._pos[-1]
        t0, t1 = self._t[i], self._t[i + 1]
        if t1 - t0 > self.max_gap_s:
            return self._pos[i]
        f = (t - t0) / (t1 - t0)
        (lat0, lon0), (lat1, lon1) = self._pos[i], self._pos[i + 1]
        return lat0 + (lat1 - lat0) * f, lon0 + (lon1 - lon0) * f
