"""
The ``[obs_columns]`` table: which columns of a vehicle's logger CSV hold
what, and which file in the logger's folder is today's.  Live obs-file
watching (the GUI and the headless publisher) and ARCHIVE's vehicle-obs files
(archive/vehicle_obs.py) read files with the same map.  See
https://bobbysaba.com/docs/storm/ref/configuration/.

The table is one layout, or several named ones ([obs_columns.<name>]) to
choose from in the launch window (headless: ``layout``).

Standard library only: this package runs on a Pi without the rest of STORM.
"""

import re
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone

from .config_keys import Warn, check_keys


class ObsColumnsError(ValueError):
    """Raised when the [obs_columns] table is invalid."""


@dataclass(frozen=True)
class FieldMap:
    """
    One logger-file layout: logger CSV column names for the Observation
    fields, and the file to read.  An empty name means the file has no such
    column.

    Defaults are a plain CSV named after the UTC day (``*YYYYMMDD.csv``) with
    an ISO 8601 ``time`` column and STORM's field names as column names.
    Values are read as °C, m/s, hPa and degrees unless ``units`` says
    otherwise ([obs_columns.units]); to_storm() converts.
    """
    lat:           str = "lat"
    lon:           str = "lon"
    # one ISO 8601 column (UTC when it has no zone)
    timestamp:     str = "time"
    # or split date + time columns, read with the strptime formats below and
    # taken as UTC
    date_col:      str = ""
    time_col:      str = ""
    date_format:   str = "%Y%m%d"
    time_format:   str = "%H%M%S"
    # met fields
    temperature_c: str = "temperature_c"
    dewpoint_c:    str = "dewpoint_c"
    wind_speed_ms: str = "wind_speed_ms"
    wind_dir_deg:  str = "wind_dir_deg"
    pressure_mb:   str = "pressure_mb"
    heading_deg:   str = "heading_deg"
    # (field, unit) for fields not in STORM's units, e.g. ("temperature_c", "F")
    units: tuple[tuple[str, str], ...] = ()
    # today's file in the logger's folder: a file-name pattern (* and ?) where
    # {date} is the UTC day as YYYYMMDD, shortest match first; without {date},
    # the newest matching file (a logger that keeps writing to the file it
    # opened, past midnight)
    file:          str = "*{date}.csv"
    # name in the launch window's layout list
    label:         str = ""

    def to_storm(self, field: str, value: float | None) -> float | None:
        """*value* of *field* in STORM's units."""
        return convert(field, value, dict(self.units).get(field, ""))

    @property
    def daily(self) -> bool:
        """The file is picked by the UTC day in its name."""
        return "{date}" in self.file

    def file_glob(self, day: datetime) -> str:
        """The file pattern for *day*."""
        return self.file.replace("{date}", day.strftime("%Y%m%d"))


# [obs_columns] key -> FieldMap field
_KEYS = {
    "lat": "lat", "lon": "lon", "date": "date_col", "time": "time_col",
    "date_format": "date_format", "time_format": "time_format", "timestamp": "timestamp",
    "temperature_c": "temperature_c", "dewpoint_c": "dewpoint_c",
    "wind_speed_ms": "wind_speed_ms", "wind_dir_deg": "wind_dir_deg",
    "pressure_mb": "pressure_mb", "heading_deg": "heading_deg",
    "file": "file", "label": "label",
}
assert set(_KEYS.values()) == {f.name for f in fields(FieldMap)} - {"units"}
_LAYOUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Units STORM converts from, by the field they can be given for: unit name
# (lower case) -> function to STORM's unit. STORM's own unit is in each list.
_F_TO_C = lambda v: (v - 32.0) * 5.0 / 9.0  # noqa: E731
_TEMPERATURE = {"c": lambda v: v, "f": _F_TO_C, "k": lambda v: v - 273.15}
_PRESSURE = {"hpa": lambda v: v, "mb": lambda v: v, "kpa": lambda v: v * 10.0,
             "pa": lambda v: v / 100.0, "inhg": lambda v: v * 33.8639}
UNITS = {
    "temperature_c": _TEMPERATURE,
    "dewpoint_c": _TEMPERATURE,
    "wind_speed_ms": {"m/s": lambda v: v, "kt": lambda v: v * 0.514444,
                      "mph": lambda v: v * 0.44704, "km/h": lambda v: v / 3.6},
    "pressure_mb": _PRESSURE,
    "station_pressure_mb": _PRESSURE,
    "elevation_m": {"m": lambda v: v, "ft": lambda v: v * 0.3048},
}
# sounding levels ([[sources]] kind sounding, data/sources.py)
UNITS["height_m"] = UNITS["elevation_m"]
UNITS["u_ms"] = UNITS["v_ms"] = UNITS["wind_speed_ms"]


def convert(field: str, value: float | None, unit: str) -> float | None:
    """*value* of *field*, given in *unit* ("" = STORM's), in STORM's unit."""
    if value is None or not unit:
        return value
    return UNITS[field][unit.lower()](value)


def parse_units(table, name: str, allowed) -> tuple[tuple[str, str], ...]:
    """A units table ({field: unit}) checked against UNITS, for the fields in
    *allowed*."""
    if not isinstance(table, dict):
        raise ObsColumnsError(f"[{name}] must be a table")
    out = []
    for key, unit in table.items():
        if key not in allowed or key not in UNITS:
            convertible = sorted(k for k in allowed if k in UNITS)
            raise ObsColumnsError(f"[{name}] {key}: not a field with units (one of {', '.join(convertible)})")
        if not isinstance(unit, str) or unit.strip().lower() not in UNITS[key]:
            raise ObsColumnsError(f"[{name}] {key} must be one of {', '.join(UNITS[key])}")
        out.append((key, unit.strip().lower()))
    return tuple(out)


def parse_obs_columns(table, name: str = "obs_columns", warn: Warn | None = None) -> FieldMap:
    """The FieldMap for one layout table; keys left out keep the defaults,
    and "" means the file has no such column. A table with date or time
    columns and no timestamp key reads the date and time columns. An
    unknown key is an error, or with *warn* a warning and left out."""
    if not isinstance(table, dict):
        raise ObsColumnsError(f"[{name}] must be a table")
    table = check_keys(table, set(_KEYS) | {"units"}, f"[{name}]", ObsColumnsError, warn)
    changes = {}
    if "units" in table:
        changes["units"] = parse_units(table["units"], f"{name}.units", _KEYS)
    for key, value in table.items():
        if key == "units":
            continue
        if not isinstance(value, str):
            raise ObsColumnsError(f"[{name}] {key} must be a string")
        changes[_KEYS[key]] = value.strip()
    if ("date" in table or "time" in table) and "timestamp" not in table:
        changes["timestamp"] = ""
    out = replace(FieldMap(), **changes)
    if not out.lat or not out.lon:
        raise ObsColumnsError(f"[{name}] lat and lon can't be empty")
    if not out.timestamp and not (out.date_col and out.time_col):
        raise ObsColumnsError(f"[{name}] needs a timestamp column, or date and time columns")
    if not out.timestamp:
        for key in ("date_format", "time_format"):
            if not getattr(out, key):
                raise ObsColumnsError(f"[{name}] {key} can't be empty")
    rest = out.file.replace("{date}", "")
    if not out.file or "/" in out.file or "\\" in out.file or "{" in rest or "}" in rest:
        raise ObsColumnsError(f"[{name}] file must be a file-name pattern such as \"*{{date}}.csv\" "
                              "({date} is the only placeholder)")
    return out


def parse_obs_layouts(table, name: str = "obs_columns",
                      warn: Warn | None = None) -> tuple[tuple[str, FieldMap], ...]:
    """The layouts in an [obs_columns] table, in file order: one named
    "default" for a plain table, or one per [obs_columns.<name>] table.
    With *warn*, an invalid named layout is a warning and left out (the
    default layout when none is left), as are unknown keys."""
    if not isinstance(table, dict):
        raise ObsColumnsError(f"[{name}] must be a table")
    named = {k: v for k, v in table.items() if isinstance(v, dict) and k != "units"}
    if not named:
        return (("default", parse_obs_columns(table, name, warn)),)
    if len(named) != len(table):
        key = sorted(set(table) - set(named))[0]
        raise ObsColumnsError(f"[{name}] {key}: with named layouts ([{name}.<name>]), "
                              "every key goes in a layout")
    out = []
    for layout, sub in named.items():
        try:
            if not _LAYOUT_NAME_RE.match(layout):
                raise ObsColumnsError(f"[{name}.{layout}]: a layout name is lower-case letters, digits "
                                      "and _, starting with a letter")
            out.append((layout, parse_obs_columns(sub, f"{name}.{layout}", warn)))
        except ObsColumnsError as e:
            if warn is None:
                raise
            warn(f"{e} (layout left out)")
    return tuple(out) or (("default", FieldMap()),)


def pick_layout(layouts, layout: str = "") -> FieldMap:
    """The layout named *layout*, else the first."""
    return dict(layouts).get(layout) or layouts[0][1]


def row_time(row: dict, fields: FieldMap) -> datetime | None:
    """A row's UTC time from the map's columns, or None when it has none
    that can be read."""
    if fields.timestamp:
        raw = (row.get(fields.timestamp) or "").strip()
        if not raw:
            return None
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)

    date_str = (row.get(fields.date_col) or "").strip()
    time_str = (row.get(fields.time_col) or "").strip()
    if not date_str or not time_str:
        return None
    # loggers drop leading zeros from numeric columns ("50625" for 050625)
    if fields.date_format == "%d%m%y":
        date_str = date_str.zfill(6)
    if fields.time_format == "%H%M%S":
        time_str = time_str.zfill(6)
    try:
        when = datetime.strptime(f"{date_str} {time_str}", f"{fields.date_format} {fields.time_format}")
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
