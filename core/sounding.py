
from dataclasses import dataclass
from datetime import datetime

import numpy as np


# pressure levels requested from the API (hPa), surface → tropopause.
PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 725,
    700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100,
]

# time slots: current run F0 (analysis) through F3.
SOUNDING_SLOTS = [
    (0, "F0", "Analysis (F0)"),
    (1, "F1", "F+1h"),
    (2, "F2", "F+2h"),
    (3, "F3", "F+3h"),
]


@dataclass
class Sounding:
    """Vertical atmospheric profile at a single location and valid time.

    All arrays are ordered from highest pressure (surface) to lowest (top),
    with any below-ground levels already removed.
    """
    lat:         float
    lon:         float
    valid_time:  datetime
    slot_offset: int        # hour offset from analysis (−2 … +3)
    label:       str        # human-readable tab label, e.g. "Analysis −2h"

    # vertical profile arrays — all shape (N,)
    pressure:    np.ndarray  # hPa
    temperature: np.ndarray  # °C
    dewpoint:    np.ndarray  # °C
    u_wind:      np.ndarray  # m/s  (eastward component)
    v_wind:      np.ndarray  # m/s  (northward component)
    height:      np.ndarray  # m    (geopotential height MSL)

    # how a mobile launch was placed: None keeps the set-location fallback,
    # "unknown" stops it borrowing another launch's position
    location_source: str | None = None
    location_time:   datetime | None = None    # the position fix's time

    @property
    def wind_speed(self) -> np.ndarray:
        """Wind speed in m/s derived from U/V components."""
        return np.sqrt(self.u_wind ** 2 + self.v_wind ** 2)

    @property
    def wind_direction(self) -> np.ndarray:
        """Meteorological wind direction in degrees (0 = from north)."""
        direction = np.degrees(np.arctan2(-self.u_wind, -self.v_wind)) % 360
        return direction

@dataclass
class SoundingSet:
    """Sounding time slots for a single location.

    For HRRR model soundings: F0–F3 forecast slots from open-meteo.
    For observed soundings: all available raob times for the station/date
    from IEM, sorted chronologically.

    Not all slots are guaranteed to be present.
    """
    lat:       float
    lon:       float
    elevation: float      # m MSL
    fetch_time: datetime
    soundings: list       # list[Sounding], chronological order

    # populated only for observed / provider soundings:
    station_id:   str = ""      # e.g. "OUN" or "CLAMPS"
    station_name: str = ""      # e.g. "Norman, OK"
    # "hrrr" | "obs" | "provider" (a provider's live_sounding_fetcher /
    # archive soundings: NSSL's lidar truck) | "retrieval" (a provider's
    # remote-sensing profiles) | "uas" (a provider's uncrewed-aircraft ascents)
    source:       str = "hrrr"
    # a provider's name for the instrument, for the window's title and
    # header ("CLAMPS TROPoe", "CopterSonde"); retrieval and uas sets
    label:        str = ""

    @property
    def is_observed(self) -> bool:
        """True when this set came from a radiosonde station (not HRRR)."""
        return self.source == "obs"

    @property
    def is_provider(self) -> bool:
        """True when this set came from a provider's soundings (NSSL: the
        lidar truck's sondes)."""
        return self.source == "provider"

    @property
    def is_retrieval(self) -> bool:
        """True for a remote-sensing retrieval (NSSL: CLAMPS TROPoe): not an
        in-situ launch, so kept apart from a provider's sondes (a different
        comparison key, and the difference stays visible). No wind."""
        return self.source == "retrieval"

    @property
    def is_uas(self) -> bool:
        """True for an uncrewed-aircraft ascent (NSSL: CopterSonde)."""
        return self.source == "uas"

    @property
    def is_time_series(self) -> bool:
        """Soundings at observed times (labeled by clock time), not HRRR
        forecast hours."""
        return self.source != "hrrr"

    def get(self, slot_offset: int):
        """Return the Sounding for a given hour offset, or None."""
        for s in self.soundings:
            if s.slot_offset == slot_offset:
                return s
        return None
