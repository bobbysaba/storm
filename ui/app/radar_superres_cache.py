"""Archive mode's sharp radar renders, most recent first."""

from collections import OrderedDict


class RadarSuperresCache:
    """Bounded LRU of sharp (cropped, 2048-4096 px) radar PNGs.

    Keyed by station, field, tilt, scan time and processing, so two stations,
    products, tilts or times, or raw and processed velocity of one scan, never
    collide, and pausing again on a frame already sharpened costs nothing.
    """

    def __init__(self, capacity: int = 12):
        self._capacity = max(1, int(capacity))
        self._entries: "OrderedDict[tuple, tuple[bytes, list]]" = OrderedDict()

    @staticmethod
    def make_key(station: str, pyart_field: str, tilt_deg: float, scan_time, processing: str = "") -> tuple:
        return (station, pyart_field, round(float(tilt_deg), 2), scan_time.isoformat(), processing)

    def get(self, key: tuple):
        """(png_bytes, bounds), or None; a hit becomes the most recently used."""
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(self, key: tuple, png_bytes: bytes, bounds: list) -> None:
        self._entries[key] = (png_bytes, bounds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
