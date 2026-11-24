"""Display units from [units] in storm.toml. STORM keeps data in °C, m/s
and km; these convert only for showing them. Wind is always in knots."""
from __future__ import annotations

import settings

KM_PER_MI = 1.609344


def fahrenheit() -> bool:
    return settings.SETTINGS.units.temperature == "F"


def temp(celsius: float | None) -> float | None:
    """A temperature in the display unit."""
    if celsius is None:
        return None
    return celsius * 9 / 5 + 32 if fahrenheit() else celsius


def temp_label() -> str:
    return "°F" if fahrenheit() else "°C"


def miles() -> bool:
    return settings.SETTINGS.units.distance == "mi"


def dist(km: float) -> float:
    """A distance in kilometres, in the display unit."""
    return km / KM_PER_MI if miles() else km


def dist_label() -> str:
    return "mi" if miles() else "km"


def fmt_dist(km: float) -> str:
    """'4.2 mi' / '12 km': one decimal under 10."""
    d = dist(km)
    return f"{d:.1f} {dist_label()}" if d < 10 else f"{d:.0f} {dist_label()}"
