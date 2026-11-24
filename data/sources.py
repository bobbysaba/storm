"""
Data sources a team adds in storm.toml without code: ``[[sources]]`` tables,
each of a ``kind`` STORM already draws. Every key is in https://bobbysaba.com/docs/storm/ref/your-sources/.

    station_obs   station plots in SURFACE, from a JSON or CSV feed
    overlay       GeoJSON points, lines and polygons on the map
    sounding      launches for the Skew-T viewer, from an index and profiles
    contours      runs of gridded fields drawn as contour lines, from vector
                  tiles (.pmtiles or .mbtiles) per field; at most one

This module parses and checks the tables (settings/ calls parse_sources),
reads a station_obs response into STORM's observations (read_station_obs)
and sounding responses into soundings (read_launches, profile_levels,
levels_to_sounding). A code provider (https://bobbysaba.com/docs/storm/extend/providers/) makes the same
kinds with code_source(), its own fetch() in place of a URL. Fetching is
data/fetchers/surface_fetcher.py, overlay_fetcher.py and
source_sounding_fetcher.py and contour_fetcher.py. Qt-free.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import ssl
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from data import auth
from data.auth import Auth
from core.obs_cleanup import clean_value, plausible_dewpoint, valid_position
from core.observation import Observation
from core.sounding import Sounding, SoundingSet
from headless.storm_headless.config_keys import Warn, check_keys
from headless.storm_headless.obs_columns import UNITS, ObsColumnsError, convert, parse_units

KINDS = ("station_obs", "overlay", "sounding", "contours")
SHAPES = ("rows", "keyed", "columns")
# toolbar icons a source can use with where = "toolbar" (ui/launch/icons.py)
ICONS = ("surface", "radar", "satellite", "hazards", "contours", "sounding", "lidar",
         "track", "vehicles", "prev_locs", "annotate", "map")
# ids STORM uses itself
_RESERVED_IDS = {"asos"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# [sources.fields] keys for station_obs: STORM's field -> what it is
STATION_FIELDS = (
    "station", "name", "lat", "lon", "time", "elevation_m",
    "temperature_c", "dewpoint_c", "humidity_pct",
    "wind_speed_ms", "wind_dir_deg", "pressure_mb", "station_pressure_mb",
)
_MET_FIELDS = ("temperature_c", "dewpoint_c", "wind_speed_ms", "wind_dir_deg", "pressure_mb")

# [sources.fields] keys for a sounding's levels, and [sources.launch_fields]
# keys for its index of launches
SOUNDING_FIELDS = (
    "pressure_mb", "height_m", "temperature_c", "dewpoint_c", "humidity_pct",
    "wind_dir_deg", "wind_speed_ms", "u_ms", "v_ms",
)
LAUNCH_FIELDS = ("time", "url", "lat", "lon")
SOUNDING_FORMATS = ("sharppy", "csv", "json")
CONTOUR_TILES = ("pmtiles", "mbtiles")
# fewer levels than this isn't a profile
MIN_LEVELS = 5

DEFAULT_FRESH_MIN = (5.0, 10.0)


class SourceError(ValueError):
    """A [[sources]] table is invalid."""


@dataclass(frozen=True)
class StationFile:
    """Where a station_obs source's station list comes from (stations_url)."""
    url: str
    format: str = "json"
    shape: str = "rows"
    rows: str = ""
    # STORM's field -> paths to try in order
    fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    refresh_min: float = 1440.0


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    name: str
    short: str
    url: str
    # environment variable (or .env key) holding the API key, sent as
    # api_key_header, or as the query parameter api_key_param
    api_key_env: str = ""
    api_key_header: str = "X-API-Key"
    api_key_param: str = ""
    # extra CA certificate to trust for this source's server
    ca_cert: Path | None = None
    refresh_min: float = 5.0
    where: str = "drawer"
    icon: str = ""
    # -- station_obs
    format: str = "json"
    shape: str = "rows"
    rows: str = ""
    time: str = ""
    time_format: str = ""
    timezone: str = "UTC"
    fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    units: tuple[tuple[str, str], ...] = ()
    missing: tuple[str, ...] = ()
    stations: StationFile | None = None
    fresh_min: tuple[float, float] = DEFAULT_FRESH_MIN
    # -- overlay
    style: dict = field(default_factory=dict)
    # also in ARCHIVE: fixed layers such as field boundaries
    archive: bool = False
    # -- sounding (format, shape, rows, time, fields, units and missing
    # describe each profile)
    # url is an index of launches (False: url is one profile)
    index: bool = True
    launches: str = ""
    launch_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    lookback_hours: float = 12.0
    # where launches without a position of their own are
    site: tuple[float, float] | None = None
    # -- contours: the tile file each field comes in, and how run ids
    # (index.json "runs") read as times
    tiles: str = "pmtiles"
    run_format: str = "%Y%m%d_%H"
    # -- a code provider's source: its name, and the fetch() it calls in
    # place of reading url (code_source)
    provider: str = ""
    fetch: Callable | None = field(default=None, compare=False, repr=False)

    def auth(self, url: str | None = None) -> Auth | None:
        """How to ask *url* (default: the source's url): the source's own
        api_key_env / ca_cert when it sets one, else the [[auth]] table
        covering that URL (data/auth.py)."""
        if self.api_key_env or self.ca_cert is not None:
            return Auth(url=self.url, api_key_env=self.api_key_env, api_key_header=self.api_key_header,
                        api_key_param=self.api_key_param, ca_cert=self.ca_cert)
        return auth.for_url(url or self.url)

    @property
    def api_key(self) -> str:
        found = self.auth()
        return found.api_key if found is not None else ""

    def missing_key(self) -> str:
        """The environment variable this source needs but doesn't have, or ""."""
        found = self.auth()
        return found.missing_key() if found is not None else ""

    def request(self, url: str) -> tuple[str, dict[str, str]]:
        """(url, headers) for a request to this source's server, with the key."""
        found = self.auth(url)
        return found.request(url) if found is not None else (url, {})

    def ssl_context(self, url: str | None = None) -> ssl.SSLContext | None:
        found = self.auth(url)
        return found.ssl_context() if found is not None else None


# -- parsing storm.toml --------------------------------------------------------

_COMMON_KEYS = {"id", "kind", "name", "short", "url", "api_key_env", "api_key_header",
                "api_key_param", "ca_cert", "refresh_min", "where", "icon"}
_KIND_KEYS = {
    "station_obs": {"format", "shape", "rows", "time", "time_format", "timezone", "fields",
                    "units", "missing", "stations_url", "stations_format", "stations_shape",
                    "stations_rows", "station_fields", "stations_refresh_min", "fresh_min"},
    "overlay": {"style", "archive"},
    "sounding": {"index", "launches", "launch_fields", "lookback_hours", "format", "shape",
                 "rows", "time", "time_format", "timezone", "fields", "units", "missing",
                 "lat", "lon"},
    "contours": {"tiles", "run_format"},
}
# a contours source has its own toolbar button and drawer, and no refresh
_NOT_FOR_CONTOURS = ("where", "refresh_min")
_STYLE_KEYS = {"color": str, "width": (int, float), "fill_opacity": (int, float),
               "radius": (int, float), "label": str}


def parse_sources(raw, project_dir: Path, warn: Warn | None = None) -> tuple[Source, ...]:
    """The [[sources]] array of tables, checked. Relative ca_cert paths
    resolve against *project_dir*. With *warn*, an invalid source is a
    warning and left out, and so is an unknown key or field; without,
    either is an error."""
    if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
        raise SourceError("[[sources]] must be tables (one [[sources]] header each)")
    out, seen, contours = [], set(), []
    for table in raw:
        try:
            source = _parse_source(table, project_dir, warn)
            if source.id in seen:
                raise SourceError(f"[[sources]] id {source.id!r} is used twice")
            if source.kind == "contours" and contours:
                raise SourceError(f"[[sources]] {source.id}: only one contours source is supported "
                                  f"({contours[0]} is the first)")
        except SourceError as e:
            if warn is None:
                raise
            warn(f"{e} (source left out)")
            continue
        seen.add(source.id)
        if source.kind == "contours":
            contours.append(source.id)
        out.append(source)
    return tuple(out)


def _text(table, key, where, default="", required=False) -> str:
    if key not in table:
        if required:
            raise SourceError(f"{where}: {key} is required")
        return default
    value = table[key]
    if not isinstance(value, str) or (required and not value.strip()):
        raise SourceError(f"{where}: {key} must be a string")
    return value.strip()


def _minutes(table, key, where, default) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise SourceError(f"{where}: {key} must be a number of minutes above 0")
    return float(value)


def _url(value: str, where: str, key: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise SourceError(f"{where}: {key} must be an http(s) URL")
    return value


def _field_paths(table, where, allowed, warn=None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(table, dict):
        raise SourceError(f"{where} must be a table")
    table = check_keys(table, allowed, where, SourceError, warn, what="field")
    out = []
    for key, value in table.items():
        paths = [value] if isinstance(value, str) else value
        if (not isinstance(paths, list) or not paths
                or not all(isinstance(p, str) and p.strip() for p in paths)):
            raise SourceError(f"{where} {key} must be a field name, or a list of them to try in order")
        out.append((key, tuple(p.strip() for p in paths)))
    return tuple(out)


def _parse_source(table: dict, project_dir: Path, warn: Warn | None = None) -> Source:
    where = "[[sources]]"
    sid = _text(table, "id", where, required=True)
    if not _ID_RE.match(sid) or sid in _RESERVED_IDS:
        raise SourceError(f"{where} id {sid!r} must be lower-case letters, digits, - or _ "
                          f"(and not {', '.join(sorted(_RESERVED_IDS))})")
    where = f"[[sources]] {sid}"
    kind = _text(table, "kind", where, required=True)
    if kind not in KINDS:
        raise SourceError(f"{where}: kind must be one of {', '.join(KINDS)}")
    known = _COMMON_KEYS | _KIND_KEYS[kind]
    if kind == "contours":
        known -= set(_NOT_FOR_CONTOURS)
    table = check_keys(table, known, f"{where} (kind {kind})", SourceError, warn)

    name = _text(table, "name", where, default=sid.upper())
    short = _text(table, "short", where, default=sid.upper())
    url = _url(_text(table, "url", where, required=True), where, "url")
    try:
        keys = auth.parse_key_fields(table, where, project_dir)
    except auth.AuthError as exc:
        raise SourceError(str(exc)) from None
    place = _text(table, "where", where, default="toolbar" if kind == "contours" else "drawer")
    if place not in ("drawer", "toolbar"):
        raise SourceError(f"{where}: where must be \"drawer\" or \"toolbar\"")
    icon = _text(table, "icon", where, default="contours" if kind == "contours" else "")
    icon = {"sfcoa": "contours"}.get(icon, icon)   # its name before 3.0
    if place == "toolbar" and kind == "sounding":
        raise SourceError(f"{where}: a sounding source sits in the SOUNDING drawer; "
                          "where = \"toolbar\" isn't for it")
    if place == "toolbar" and icon not in ICONS:
        raise SourceError(f"{where}: where = \"toolbar\" needs an icon, one of {', '.join(ICONS)}")
    if place != "toolbar" and icon:
        raise SourceError(f"{where}: icon is only for where = \"toolbar\"")

    common = dict(id=sid, kind=kind, name=name, short=short, url=url, **keys, where=place, icon=icon)
    if kind == "overlay":
        return Source(**common, refresh_min=_minutes(table, "refresh_min", where, 10),
                      style=_parse_style(table.get("style", {}), where, warn),
                      archive=_flag(table, "archive", where))
    if kind == "sounding":
        return _parse_sounding(table, where, common, warn)
    if kind == "contours":
        return _parse_contours(table, where, common)
    return _parse_station_obs(table, where, common, warn)


def _parse_contours(table: dict, where: str, common: dict) -> Source:
    run_format = _text(table, "run_format", where, default="%Y%m%d_%H")
    try:
        datetime.strptime(datetime(2026, 5, 7, 15, 30).strftime(run_format), run_format)
    except ValueError:
        raise SourceError(f"{where}: run_format {run_format!r} isn't a date format "
                          "(Python strftime codes, such as \"%Y%m%d_%H\")") from None
    if "%" not in run_format:
        raise SourceError(f"{where}: run_format needs date codes such as %Y")
    return Source(**common, tiles=_choice(table, "tiles", where, CONTOUR_TILES, "pmtiles"),
                  run_format=run_format)


def _flag(table, key, where, default=False) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise SourceError(f"{where}: {key} must be true or false")
    return value


def _parse_style(table, where, warn=None) -> dict:
    if not isinstance(table, dict):
        raise SourceError(f"{where} style must be a table")
    table = check_keys(table, _STYLE_KEYS, f"{where} style", SourceError, warn)
    out = {}
    for key, value in table.items():
        kind = _STYLE_KEYS[key]
        if isinstance(value, bool) or not isinstance(value, kind):
            raise SourceError(f"{where} style {key} has the wrong type")
        if key == "color" and not _COLOR_RE.match(value):
            raise SourceError(f"{where} style color must be a hex color such as \"#ff8800\"")
        if key == "fill_opacity" and not 0 <= value <= 1:
            raise SourceError(f"{where} style fill_opacity must be from 0 to 1")
        if key in ("width", "radius") and not 0 < value <= 50:
            raise SourceError(f"{where} style {key} must be above 0 (pixels)")
        out[key] = value
    return out


def _choice(table, key, where, choices, default) -> str:
    value = _text(table, key, where, default=default)
    if value not in choices:
        raise SourceError(f"{where}: {key} must be one of {', '.join(choices)}")
    return value


def _parse_station_obs(table: dict, where: str, common: dict, warn: Warn | None = None) -> Source:
    fmt = _choice(table, "format", where, ("json", "csv"), "json")
    shape = _choice(table, "shape", where, SHAPES, "rows")
    if fmt == "csv" and shape != "rows":
        raise SourceError(f"{where}: a CSV response can only have shape = \"rows\"")
    fields = _field_paths(table.get("fields", {}), f"{where} fields", STATION_FIELDS, warn)
    try:
        units = parse_units(table.get("units", {}), f"{where.replace('[[sources]]', 'sources')} units",
                            STATION_FIELDS)
    except ObsColumnsError as e:
        raise SourceError(str(e)) from None
    tz = _text(table, "timezone", where, default="UTC")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise SourceError(f"{where}: timezone {tz!r} is not a time zone name such as "
                          "\"UTC\" or \"America/Chicago\"") from None
    missing = table.get("missing", [])
    if not isinstance(missing, list) or not all(
            isinstance(m, (str, int, float)) and not isinstance(m, bool) for m in missing):
        raise SourceError(f"{where}: missing must be a list of values such as [-999, \"M\"]")
    fresh = table.get("fresh_min", list(DEFAULT_FRESH_MIN))
    if (not isinstance(fresh, list) or len(fresh) != 2
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in fresh)
            or fresh[0] > fresh[1]):
        raise SourceError(f"{where}: fresh_min must be two minutes, green then amber, such as [5, 10]")

    stations = None
    station_keys = [k for k in table if k.startswith("station")]
    if "stations_url" in table:
        s_fmt = _choice(table, "stations_format", where, ("json", "csv"), "json")
        s_shape = _choice(table, "stations_shape", where, ("rows", "keyed"), "rows")
        if s_fmt == "csv" and s_shape != "rows":
            raise SourceError(f"{where}: a CSV station list can only have stations_shape = \"rows\"")
        s_fields = _field_paths(table.get("station_fields", {}), f"{where} station_fields",
                                STATION_FIELDS, warn)
        stations = StationFile(
            url=_url(_text(table, "stations_url", where, required=True), where, "stations_url"),
            format=s_fmt, shape=s_shape, rows=_text(table, "stations_rows", where),
            fields=s_fields, refresh_min=_minutes(table, "stations_refresh_min", where, 1440))
    elif station_keys:
        raise SourceError(f"{where}: {sorted(station_keys)[0]} needs stations_url")

    have = {k for k, _ in fields} | ({k for k, _ in stations.fields} if stations else set())
    if shape == "rows" and "station" not in have:
        raise SourceError(f"{where}: shape = \"rows\" needs a station field (each row's station id)")
    if stations and "station" not in dict(stations.fields) and stations.shape == "rows":
        raise SourceError(f"{where}: station_fields needs station (each station's id)")
    for need in ("lat", "lon"):
        if need not in have:
            raise SourceError(f"{where}: needs a {need} field, in fields or station_fields")
    if not have & set(_MET_FIELDS + ("humidity_pct", "station_pressure_mb")):
        raise SourceError(f"{where}: fields has no readings (temperature_c, wind_speed_ms, ...)")
    if "station_pressure_mb" in have and "elevation_m" not in have:
        raise SourceError(f"{where}: station_pressure_mb needs an elevation_m field")

    return Source(**common, refresh_min=_minutes(table, "refresh_min", where, 5),
                  format=fmt, shape=shape, rows=_text(table, "rows", where),
                  time=_text(table, "time", where),
                  time_format=_text(table, "time_format", where), timezone=tz,
                  fields=fields, units=units, missing=tuple(str(m) for m in missing),
                  stations=stations, fresh_min=(float(fresh[0]), float(fresh[1])))


def _parse_sounding(table: dict, where: str, common: dict, warn: Warn | None = None) -> Source:
    fmt = _choice(table, "format", where, SOUNDING_FORMATS, "sharppy")
    index = _flag(table, "index", where, True)
    if index and "time" in table:
        raise SourceError(f"{where}: time is for index = false; with an index, each launch's time "
                          "is a launch_fields entry")
    if fmt != "json" and "time" in table:
        raise SourceError(f"{where}: time (a path in the profile) needs format = \"json\"")
    if fmt == "sharppy":
        extra = sorted({"fields", "units", "shape", "rows"} & set(table))
        if extra:
            raise SourceError(f"{where}: format = \"sharppy\" has fixed columns; {extra[0]} is for "
                              "csv or json profiles")
    shape = _choice(table, "shape", where, ("rows", "arrays"), "rows")
    if fmt == "csv" and shape != "rows":
        raise SourceError(f"{where}: a CSV profile can only have shape = \"rows\"")
    fields = _field_paths(table.get("fields", {}), f"{where} fields", SOUNDING_FIELDS, warn)
    if fmt != "sharppy":
        have = {k for k, _ in fields}
        for need in ("pressure_mb", "height_m", "temperature_c"):
            if need not in have:
                raise SourceError(f"{where}: fields needs {need}")
        if not have & {"dewpoint_c", "humidity_pct"}:
            raise SourceError(f"{where}: fields needs dewpoint_c or humidity_pct")
        for pair in (("wind_dir_deg", "wind_speed_ms"), ("u_ms", "v_ms")):
            if len(have & set(pair)) == 1:
                raise SourceError(f"{where}: fields needs both {pair[0]} and {pair[1]}, or neither")
    try:
        units = parse_units(table.get("units", {}), f"{where.replace('[[sources]]', 'sources')} units",
                            SOUNDING_FIELDS)
    except ObsColumnsError as e:
        raise SourceError(str(e)) from None
    missing = table.get("missing", [])
    if not isinstance(missing, list) or not all(
            isinstance(m, (str, int, float)) and not isinstance(m, bool) for m in missing):
        raise SourceError(f"{where}: missing must be a list of values such as [-9999, \"M\"]")
    tz = _text(table, "timezone", where, default="UTC")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise SourceError(f"{where}: timezone {tz!r} is not a time zone name such as "
                          "\"UTC\" or \"America/Chicago\"") from None

    if not index and ("launches" in table or "launch_fields" in table or "lookback_hours" in table):
        raise SourceError(f"{where}: launches, launch_fields and lookback_hours are for an index "
                          "(index = true)")
    launch_fields = _field_paths(table.get("launch_fields", {}), f"{where} launch_fields",
                                 LAUNCH_FIELDS, warn)
    lookback = table.get("lookback_hours", 12)
    if isinstance(lookback, bool) or not isinstance(lookback, (int, float)) or not 0 < lookback <= 240:
        raise SourceError(f"{where}: lookback_hours must be hours above 0 (at most 240)")

    site = None
    if "lat" in table or "lon" in table:
        lat, lon = table.get("lat"), table.get("lon")
        if (not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lat, lon))
                or not valid_position(float(lat), float(lon))):
            raise SourceError(f"{where}: lat and lon must both be given, in degrees")
        site = (float(lat), float(lon))

    return Source(**common, refresh_min=_minutes(table, "refresh_min", where, 5),
                  format=fmt, shape=shape, rows=_text(table, "rows", where),
                  time=_text(table, "time", where),
                  time_format=_text(table, "time_format", where), timezone=tz,
                  fields=fields, units=units, missing=tuple(str(m) for m in missing),
                  index=index, launches=_text(table, "launches", where),
                  launch_fields=launch_fields, lookback_hours=float(lookback), site=site)


# -- a code provider's sources -------------------------------------------------

_CODE_KEYS = {
    "station_obs": {"name", "short", "refresh_min", "where", "icon", "fresh_min"},
    "overlay": {"name", "short", "refresh_min", "where", "icon", "style", "archive"},
    "sounding": {"name", "short", "refresh_min", "lat", "lon"},
}


def code_source(kind: str, id: str, fetch: Callable, **options) -> Source:
    """A source whose data comes from *fetch()* in a code provider
    (https://bobbysaba.com/docs/storm/extend/providers/) instead of a URL. *options* are the display keys
    a [[sources]] table of that kind has: name, short, refresh_min, where,
    icon, and fresh_min (station_obs), style and archive (overlay), lat and
    lon (sounding). What fetch() returns, by kind:

        station_obs  a list of dicts keyed by STORM's field names
                     (STATION_FIELDS) in STORM's units; time a datetime or
                     ISO 8601 text
        overlay      GeoJSON (a FeatureCollection, Feature or geometry)
        sounding     a list of launches, each a dict with time, lat, lon and
                     levels: a list of dicts keyed by SOUNDING_FIELDS

    Raises SourceError for a bad id, kind or option."""
    if kind not in _CODE_KEYS:
        raise SourceError(f"code source {id!r}: kind must be one of {', '.join(_CODE_KEYS)}")
    if not callable(fetch):
        raise SourceError(f"code source {id!r}: fetch must be a function")
    unknown = set(options) - _CODE_KEYS[kind]
    if unknown:
        raise SourceError(f"code source {id!r}: unknown option {sorted(unknown)[0]} for kind {kind}")
    # a URL the table parser accepts; fetch() is what's read
    table = {"id": id, "kind": kind, "url": "https://code-provider.invalid/", **options}
    if kind == "station_obs":
        table["fields"] = {f: f for f in STATION_FIELDS}
    elif kind == "sounding":
        table.update(format="json", fields={f: f for f in SOUNDING_FIELDS})
    try:
        source = _parse_source(table, Path("."))
    except SourceError as e:
        raise SourceError(str(e).replace("[[sources]]", "code source")) from None
    return replace(source, url="", fetch=fetch)


# -- reading a station_obs response --------------------------------------------

def decode(raw: bytes, fmt: str):
    """A response body as JSON data or a list of CSV rows."""
    text = raw.decode("utf-8-sig", errors="replace")
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(text), skipinitialspace=True))
    return json.loads(text)


def get_path(data, path: str):
    """``a.b.c`` inside nested objects (list items by index); None when absent.
    An empty path is *data* itself. A key with dots in it is tried whole."""
    if not path:
        return data
    if isinstance(data, dict) and path in data:
        return data[path]
    head, _, rest = path.partition(".")
    if isinstance(data, dict):
        nxt = data.get(head)
    elif isinstance(data, list) and head.isdigit() and int(head) < len(data):
        nxt = data[int(head)]
    else:
        return None
    return get_path(nxt, rest) if rest else nxt


def station_key(value) -> str:
    """A station id as STORM keys it: lower case, other characters as _."""
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value).strip()).strip("_")


def _rows(data, shape: str, rows_path: str) -> list[tuple[str | None, dict]]:
    """(key, row) pairs; the key is the station id for keyed and columns."""
    block = get_path(data, rows_path)
    if shape == "rows":
        if not isinstance(block, list):
            raise ValueError(f"no list of rows at {rows_path or 'the top level'}")
        return [(None, r) for r in block if isinstance(r, dict)]
    if not isinstance(block, dict):
        raise ValueError(f"no object at {rows_path or 'the top level'}")
    if shape == "keyed":
        return [(k, r) for k, r in block.items() if isinstance(r, dict)]
    # columns: {field: {station: value}} -> one row per station
    by_station: dict[str, dict] = {}
    for var, values in block.items():
        if isinstance(values, dict):
            for stid, value in values.items():
                by_station.setdefault(stid, {})[var] = value
    return list(by_station.items())


def _is_missing(value, missing: tuple[str, ...]) -> bool:
    text = str(value).strip()
    if not text:
        return True
    number = _number(text)
    return any(text == m or (number is not None and number == _number(m)) for m in missing)


def _first(row: dict, paths: tuple[str, ...], missing: tuple[str, ...]):
    for path in paths:
        value = get_path(row, path)
        if value is None or isinstance(value, (dict, list)) or _is_missing(value, missing):
            continue
        return value
    return None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_stations(data, station_file: StationFile, missing=()) -> dict[str, dict]:
    """A station list as {station key: {STORM field: value}}."""
    paths = dict(station_file.fields)
    out = {}
    for key, row in _rows(data, station_file.shape, station_file.rows):
        values = {f: _first(row, p, tuple(missing)) for f, p in paths.items()}
        sid = values.pop("station", None) or key
        if sid is None or str(sid).strip() == "":
            continue
        out[station_key(sid)] = values
    return out


def _parse_time(value, source: Source, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        when = value if value.tzinfo else value.replace(tzinfo=ZoneInfo(source.timezone))
        return when.astimezone(timezone.utc)
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).strip()
        if source.time_format:
            when = datetime.strptime(text, source.time_format)
        else:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return fallback
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo(source.timezone))
    return when.astimezone(timezone.utc)


def dewpoint_from_rh(temp_c: float | None, rh_pct: float | None) -> float | None:
    if temp_c is None or rh_pct is None or not 0.0 < rh_pct <= 100.0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(rh_pct / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def station_to_mslp(pressure_mb: float | None, elevation_m: float | None,
                    temp_c: float | None) -> float | None:
    """Station pressure reduced to sea level (hypsometric, standard lapse)."""
    if pressure_mb is None or elevation_m is None or elevation_m <= 0.0:
        return pressure_mb
    t_avg_k = (temp_c if temp_c is not None else 15.0) + 273.15 + 0.5 * 0.0065 * elevation_m
    return pressure_mb * math.exp(elevation_m / (29.271 * t_avg_k))


def read_station_obs(source: Source, data, stations: dict[str, dict] | None = None,
                     now: datetime | None = None) -> list[dict]:
    """A decoded station_obs response as SurfaceFetcher items
    ({id, source, name, obs}). Rows without a station or a real position are
    skipped; readings that are missing or impossible are left empty."""
    now = now or datetime.now(timezone.utc)
    missing = source.missing
    paths = dict(source.fields)
    units = dict(source.units)
    whole_time = _parse_time(get_path(data, source.time), source, now) if source.time else now
    stations = stations or {}

    items = []
    for key, row in _rows(data, source.shape, source.rows):
        values = {f: _first(row, p, missing) for f, p in paths.items()}
        sid = values.get("station") if values.get("station") is not None else key
        if sid is None or not station_key(sid):
            continue
        sid = station_key(sid)
        meta = stations.get(sid)
        if stations and meta is None and ("lat" not in paths or "lon" not in paths):
            continue
        for f, v in (meta or {}).items():
            if values.get(f) is None:
                values[f] = v

        def num(f):
            n = _number(values.get(f))
            return convert(f, n, units.get(f, "")) if f in UNITS and n is not None else n

        lat, lon = _number(values.get("lat")), _number(values.get("lon"))
        if lat is None or lon is None or not valid_position(lat, lon):
            continue
        temp = clean_value("temperature_c", num("temperature_c"))
        dew = clean_value("dewpoint_c", num("dewpoint_c"))
        if dew is None:
            dew = clean_value("dewpoint_c", dewpoint_from_rh(temp, _number(values.get("humidity_pct"))))
        pressure = num("pressure_mb")
        if pressure is None:
            pressure = station_to_mslp(num("station_pressure_mb"), num("elevation_m"), temp)
        when = _parse_time(values.get("time"), source, whole_time) if "time" in paths else whole_time

        obs = Observation(
            vehicle_id=f"surface:{source.id}:{sid}",
            lat=lat,
            lon=lon,
            timestamp=when,
            icon_type="mesonet",
            temperature_c=temp,
            dewpoint_c=plausible_dewpoint(dew, [temp]),
            wind_speed_ms=clean_value("wind_speed_ms", num("wind_speed_ms")),
            wind_dir_deg=clean_value("wind_dir_deg", num("wind_dir_deg")),
            pressure_mb=clean_value("pressure_mb", pressure),
        )
        name = values.get("name")
        items.append({
            "id": obs.vehicle_id,
            "source": source.id,
            "name": str(name).strip() if name not in (None, "") else sid.upper(),
            "obs": obs,
        })
    return items


# -- reading sounding responses ------------------------------------------------

def sounding_key(source_id: str) -> str:
    """A sounding source's key in the Skew-T viewer (SoundingSet.source)."""
    return f"source:{source_id}"


@dataclass(frozen=True)
class Launch:
    """One entry in a sounding source's index."""
    time: datetime
    url: str
    lat: float | None = None
    lon: float | None = None


def _position(lat, lon) -> tuple[float | None, float | None]:
    lat, lon = _number(lat), _number(lon)
    if lat is None or lon is None or not valid_position(lat, lon):
        return None, None
    return lat, lon


def read_launches(source: Source, data, now: datetime | None = None) -> list[Launch]:
    """An index response as its launches within lookback_hours, oldest
    first. Each launch's url is resolved against the index's url."""
    now = now or datetime.now(timezone.utc)
    block = get_path(data, source.launches)
    if not isinstance(block, list):
        raise ValueError(f"no list of launches at {source.launches or 'the top level'}")
    paths = {f: (f,) for f in LAUNCH_FIELDS}
    paths.update(dict(source.launch_fields))
    cutoff = now - timedelta(hours=source.lookback_hours)
    out = []
    for row in block:
        if not isinstance(row, dict):
            continue
        values = {f: _first(row, p, source.missing) for f, p in paths.items()}
        when = _parse_time(values["time"], source, None)
        if values["url"] is None or when is None or not cutoff <= when <= now + timedelta(hours=1):
            continue
        lat, lon = _position(values["lat"], values["lon"])
        out.append(Launch(when, urljoin(source.url, str(values["url"]).strip()), lat, lon))
    out.sort(key=lambda launch: launch.time)
    return out


def _sharppy_levels(text: str) -> list[dict]:
    """The %RAW% block of a SHARPpy / SPC text sounding: pressure (hPa),
    height (m), temperature, dewpoint (C), wind direction, wind speed (kt)."""
    levels, in_raw = [], False
    for line in text.splitlines():
        line = line.strip()
        if line == "%RAW%":
            in_raw = True
            continue
        if line.startswith("%"):
            in_raw = False
            continue
        if not in_raw or not line:
            continue
        parts = [_number(x) for x in line.split(",")]
        if len(parts) < 6:
            continue
        values = [None if v is None or v <= -9998 else v for v in parts[:6]]
        p, z, t, td, wd, ws = values
        levels.append({"pressure_mb": p, "height_m": z, "temperature_c": t, "dewpoint_c": td,
                       "wind_dir_deg": wd,
                       "wind_speed_ms": None if ws is None else convert("wind_speed_ms", ws, "kt")})
    return levels


def _profile_rows(source: Source, data) -> list[dict]:
    block = get_path(data, source.rows)
    if source.shape == "arrays":
        if not isinstance(block, dict):
            raise ValueError(f"no object of arrays at {source.rows or 'the top level'}")
        arrays = {k: v for k, v in block.items() if isinstance(v, list)}
        size = max((len(v) for v in arrays.values()), default=0)
        return [{k: v[i] for k, v in arrays.items() if i < len(v)} for i in range(size)]
    if not isinstance(block, list):
        raise ValueError(f"no list of levels at {source.rows or 'the top level'}")
    return [row for row in block if isinstance(row, dict)]


def profile_levels(source: Source, raw: bytes) -> list[dict]:
    """A profile response as levels: {SOUNDING_FIELDS name: number or None}
    in STORM's units."""
    if source.format == "sharppy":
        return _sharppy_levels(raw.decode("utf-8-sig", errors="replace"))
    return read_levels(_profile_rows(source, decode(raw, source.format)),
                       dict(source.fields), dict(source.units), source.missing)


def read_levels(rows, paths: dict, units: dict | None = None, missing=()) -> list[dict]:
    """Rows of a profile as levels in STORM's units, read through *paths*
    ({STORM field: paths to try}) and converted from *units*."""
    units = units or {}
    levels = []
    for row in rows:
        level = {}
        for f, p in paths.items():
            n = _number(_first(row, p, tuple(missing)))
            level[f] = convert(f, n, units.get(f, "")) if f in UNITS and n is not None else n
        levels.append(level)
    return levels


def levels_to_sounding(levels: list[dict], when: datetime, lat: float | None = None,
                       lon: float | None = None) -> Sounding | None:
    """Levels as a Sounding, surface first; None with fewer than MIN_LEVELS
    usable ones. A level needs pressure, height, temperature and a dewpoint
    (or humidity); wind is direction and speed or u and v, and missing wind
    stays empty."""
    kept = {}
    for level in levels:
        p, z, t = (level.get(k) for k in ("pressure_mb", "height_m", "temperature_c"))
        t = clean_value("temperature_c", t)
        if p is None or z is None or t is None or not 1.0 <= p <= 1100.0:
            continue
        td = level.get("dewpoint_c")
        if td is None:
            td = dewpoint_from_rh(t, level.get("humidity_pct"))
        if td is None:
            continue
        u, v = level.get("u_ms"), level.get("v_ms")
        wd, ws = level.get("wind_dir_deg"), level.get("wind_speed_ms")
        if (u is None or v is None) and wd is not None and ws is not None and ws >= 0:
            u = -ws * math.sin(math.radians(wd))
            v = -ws * math.cos(math.radians(wd))
        if u is None or v is None:
            u = v = math.nan
        # one level per pressure; the first one read wins
        kept.setdefault(round(p, 2), (p, z, t, min(td, t), u, v))
    if len(kept) < MIN_LEVELS:
        return None
    rows = sorted(kept.values(), key=lambda r: -r[0])
    cols = list(zip(*rows))
    return Sounding(
        lat=lat if lat is not None else 0.0,
        lon=lon if lon is not None else 0.0,
        valid_time=when,
        slot_offset=0,
        label="",
        pressure=np.array(cols[0], dtype=np.float64),
        height=np.array(cols[1], dtype=np.float64),
        temperature=np.array(cols[2], dtype=np.float64),
        dewpoint=np.array(cols[3], dtype=np.float64),
        u_wind=np.array(cols[4], dtype=np.float64),
        v_wind=np.array(cols[5], dtype=np.float64),
    )


def profile_time(source: Source, raw: bytes, fallback: datetime) -> datetime:
    """An index = false profile's launch time: its time path (JSON), else
    *fallback*."""
    if source.time and source.format == "json":
        return _parse_time(get_path(decode(raw, "json"), source.time), source, fallback)
    return fallback


def code_soundings(source: Source, launches) -> list[Sounding]:
    """A code provider's sounding fetch() result as Soundings."""
    out = []
    for launch in launches or ():
        if not isinstance(launch, dict):
            continue
        when = _parse_time(launch.get("time"), source, None)
        if when is None:
            continue
        lat, lon = _position(launch.get("lat"), launch.get("lon"))
        rows = [row for row in launch.get("levels") or () if isinstance(row, dict)]
        levels = read_levels(rows, dict(source.fields), missing=source.missing)
        snd = levels_to_sounding(levels, when, lat, lon)
        if snd is not None:
            out.append(snd)
    return out


def sounding_set(source: Source, soundings: list[Sounding], now: datetime) -> SoundingSet:
    """A source's soundings as the set the Skew-T viewer shows, oldest
    first. Soundings without a position get the source's site; the set sits
    where the latest positioned one is."""
    soundings = sorted(soundings, key=lambda snd: snd.valid_time)
    for i, snd in enumerate(soundings):
        if snd.lat == 0.0 and snd.lon == 0.0 and source.site is not None:
            snd.lat, snd.lon = source.site
        snd.slot_offset = i
        snd.label = f"{snd.valid_time:%H%M}Z {snd.valid_time:%b} {snd.valid_time.day}"
    placed = [snd for snd in soundings if snd.lat != 0.0 or snd.lon != 0.0]
    lat, lon = (placed[-1].lat, placed[-1].lon) if placed else (0.0, 0.0)
    return SoundingSet(
        lat=lat,
        lon=lon,
        elevation=float(soundings[0].height[0]) if soundings else 0.0,
        fetch_time=now,
        soundings=soundings,
        station_id=source.short,
        station_name=source.name,
        source=sounding_key(source.id),
    )
