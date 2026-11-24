"""
Generic sanity checks for a reading: missing-value codes, impossible values,
a dewpoint above the temperature, null-island positions. Shared by
ARCHIVE's vehicle-obs files (archive/vehicle_obs.py) and the ``station_obs``
sources (data/sources.py). Qt-free.
"""

from __future__ import annotations

import math

# Values dropped as not being measurements -- only ones no real reading
# could produce, since genuine extremes (e.g. >50 m/s winds near tornadoes)
# must survive. Loggers commonly write -999 when a sensor drops out and 999
# for a calm or dead anemometer; beyond those, a failed fast-response
# thermistor reads -273.1 or -110 C, and no surface wind has ever been
# measured near 120 m/s.
_MISSING_AT_OR_BELOW = -900.0
_MISSING_WIND_AT_OR_ABOVE = 999.0
PHYSICAL_LIMITS = {
    "temperature_c": (-60.0, 60.0),
    "dewpoint_c": (-80.0, 40.0),
    "wind_speed_ms": (0.0, 120.0),
    "wind_dir_deg": (0.0, 360.0),
    "pressure_mb": (500.0, 1100.0),
    "heading_deg": (0.0, 360.0),
}
# A dewpoint can't meaningfully exceed the air temperature; allow for
# sensors disagreeing slightly.
DEWPOINT_OVER_TEMPERATURE_C = 1.0


def float_or_none(value, missing: tuple[str, ...] = ()) -> float | None:
    """*value* as a float, or None when it's None, blank, one of *missing*
    (a feed's missing-value markers, e.g. "M") or not a number."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value in missing:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_value(field: str, value) -> float | None:
    """A reading for an Observation field, or None when it's a missing-value
    code or impossible (PHYSICAL_LIMITS)."""
    number = float_or_none(value)
    if number is None or not math.isfinite(number) or number <= _MISSING_AT_OR_BELOW:
        return None
    if field in ("wind_speed_ms", "wind_dir_deg") and number >= _MISSING_WIND_AT_OR_ABOVE:
        return None
    if field in ("wind_dir_deg", "heading_deg") and -360.0 < number < 720.0:
        number %= 360.0  # a few degrees past north either way is still a direction
    low, high = PHYSICAL_LIMITS[field]
    return number if low <= number <= high else None


def plausible_dewpoint(dewpoint: float | None, temperatures) -> float | None:
    temperatures = [t for t in temperatures if t is not None]
    if dewpoint is not None and temperatures and dewpoint > max(temperatures) + DEWPOINT_OVER_TEMPERATURE_C:
        return None
    return dewpoint


def valid_position(lat: float, lon: float) -> bool:
    """Real coordinates, and not a receiver's null-island placeholders
    (exactly 0,0 or a partial fix within a degree or two of it)."""
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return False
    return not (abs(lat) < 2 and abs(lon) < 2)
