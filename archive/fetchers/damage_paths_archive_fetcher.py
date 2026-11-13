"""Damage-survey paths for ARCHIVE: tornado tracks inside a box drawn on the
map, on the session's date, ported from the storm_ty_edits fork (itself from
MESO-VIEW's dat_tracks.py).

The source is NOAA's Damage Assessment Toolkit (DAT) damage lines, an ArcGIS
FeatureServer queried by box and date. DAT is sparse before about 2015, so
when it has nothing the fallback is NCEI Storm Events: each tornado's begin
and end points from the year's bulk details CSV (read once per year, then
kept in memory, so redrawing the box doesn't download it again).

Features come back as one GeoJSON FeatureCollection of LineStrings, each with
``ef_label`` / ``ef_color`` (the usual EF-scale colors) and ``damage_source``
added for the map's paint and hover (ui/map/map_template.html).
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import math
import re
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

import config
from core import package_sources
from core.qt_signals import emit_alive
from data import endpoints

log = logging.getLogger(__name__)

DAT_DAMAGE_LINES_URL = endpoints.DAT_DAMAGE_LINES
NCEI_EVENT_CSV_BASE_URL = endpoints.NCEI_STORM_EVENTS_CSV

# +/- one day around the session date: survey times are approximate, and the
# session itself runs into the next UTC morning (archive/session.py).
_DATE_TOLERANCE_DAYS = 1
_REQUEST_TIMEOUT = 20
_NCEI_TIMEOUT = 60
_USER_AGENT = "Mozilla/5.0 STORM/1.0"

DEFAULT_OUT_FIELDS = [
    "objectid", "globalid", "event_id", "stormdate", "starttime", "endtime",
    "startlat", "startlon", "endlat", "endlon", "length", "width",
    "injuries", "fatalities", "efscale", "efnum", "maxwind", "wfo", "comments",
]

# The usual NWS EF-scale colors.
EF_COLORS = {
    "EF0": "#00ffc5", "EF1": "#55ff00", "EF2": "#ffff00", "EF3": "#e69800",
    "EF3+": "#e69800", "EF4": "#e60000", "EF5": "#a80084",
    "F0": "#00ffc5", "F1": "#55ff00", "F2": "#ffff00", "F3": "#e69800",
    "F4": "#e60000", "F5": "#a80084",
    "UNKNOWN": "#bee8ff", "N/A": "#bee8ff",
}

# DAT writes -99 for "no value" (width, maxwind, efnum when unrated); none of
# these can be negative, so a negative value is missing.
_NUMERIC_SENTINEL_FIELDS = ("length", "width", "injuries", "fatalities", "maxwind")

# Storm Events times are local, in the zone CZ_TIMEZONE names. Most records
# use standard-time abbreviations even in summer, so each is a fixed offset.
NCEI_TIMEZONE_OFFSETS_HOURS = {
    "UTC": 0.0, "GMT": 0.0, "Z": 0.0,
    "EST": -5.0, "EDT": -4.0, "CST": -6.0, "CDT": -5.0,
    "MST": -7.0, "MDT": -6.0, "PST": -8.0, "PDT": -7.0,
    "AKST": -9.0, "AKDT": -8.0, "AST": -4.0, "ADT": -3.0,
    "HST": -10.0, "HDT": -9.0, "SST": -11.0, "CHST": 10.0,
}

EMPTY_FC = {"type": "FeatureCollection", "features": []}

# year -> that year's tornado features (unfiltered), from the NCEI CSV
_ncei_year_cache: dict[int, list[dict]] = {}
_ncei_lock = threading.Lock()


def _to_float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _sentinel_to_none(value: float | None) -> float | None:
    return None if value is not None and value < 0 else value


def _normalize_ef(props: dict) -> str:
    raw = props.get("efscale") or props.get("efnum") or props.get("rating") or "UNKNOWN"
    s = str(raw).strip().upper()
    if not s or s in {"NONE", "NULL", "NAN", "?"} or (s.lstrip("-").isdigit() and float(s) < 0):
        return "UNKNOWN"
    if s.isdigit():
        return f"EF{s}"
    return s


def _normalize_feature(feature: dict, *, source: str) -> dict:
    f = dict(feature)
    props = dict(f.get("properties", {}) or {})
    for key in _NUMERIC_SENTINEL_FIELDS:
        if key in props:
            props[key] = _sentinel_to_none(_to_float(props.get(key)))
    ef = _normalize_ef(props)
    props["ef_label"] = ef
    props["ef_color"] = EF_COLORS.get(ef, EF_COLORS["UNKNOWN"])
    props["damage_source"] = source
    f["properties"] = props
    return f


def _parse_arcgis_time(value) -> datetime | None:
    """ArcGIS dates are epoch milliseconds (seconds accepted too)."""
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    seconds = v / 1000.0 if abs(v) > 10_000_000_000 else v
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _feature_date(props: dict) -> datetime | None:
    for key in ("starttime", "stormdate", "endtime"):
        ts = _parse_arcgis_time(props.get(key))
        if ts is not None:
            return ts
    return None


def _iter_line_coords(geom: dict):
    if not isinstance(geom, dict):
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not isinstance(coords, list):
        return
    lines = [coords] if gtype == "LineString" else coords if gtype == "MultiLineString" else []
    for line in lines:
        if not isinstance(line, list):
            continue
        for xy in line:
            if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                lon, lat = _to_float(xy[0]), _to_float(xy[1])
                if lon is not None and lat is not None:
                    yield lon, lat


def _feature_intersects_bbox(feature: dict, west: float, south: float, east: float, north: float) -> bool:
    """Whether a path touches the box: a vertex inside, or its extent
    overlapping the box (a line crossing it with both ends outside), or a
    start / end point inside."""
    xs, ys = [], []
    for lon, lat in _iter_line_coords(feature.get("geometry", {})):
        if west <= lon <= east and south <= lat <= north:
            return True
        xs.append(lon)
        ys.append(lat)
    if xs and max(xs) >= west and min(xs) <= east and max(ys) >= south and min(ys) <= north:
        return True
    props = feature.get("properties", {}) or {}
    for lon_key, lat_key in (("startlon", "startlat"), ("endlon", "endlat")):
        lon, lat = _to_float(props.get(lon_key)), _to_float(props.get(lat_key))
        if lon is not None and lat is not None and west <= lon <= east and south <= lat <= north:
            return True
    return False


def _date_window(day: date, tolerance_days: int) -> tuple[datetime, datetime]:
    lo = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(days=tolerance_days)
    return lo, lo + timedelta(days=1 + 2 * tolerance_days)


def _date_within_tolerance(feature: dict, day: date, tolerance_days: int) -> bool:
    ts = _feature_date(feature.get("properties", {}) or {})
    if ts is None:
        return True  # an undated feature isn't excluded on date alone
    lo, hi = _date_window(day, tolerance_days)
    return lo <= ts <= hi


def _arcgis_query_url(west: float, south: float, east: float, north: float,
                      day: date, tolerance_days: int) -> str:
    """The box and the date window both go to the server: the layer holds
    every survey since 2008 and answers at most 2000 features, so filtering
    dates afterwards could miss the case's paths in a busy box."""
    lo, hi = _date_window(day, tolerance_days)
    stamp = "%Y-%m-%d %H:%M:%S"
    span = (f"starttime >= TIMESTAMP '{lo.strftime(stamp)}' AND starttime < TIMESTAMP '{hi.strftime(stamp)}'")
    storm_span = span.replace("starttime", "stormdate")
    geometry = {
        "xmin": west, "ymin": south, "xmax": east, "ymax": north,
        "spatialReference": {"wkid": 4326},
    }
    params = {
        "f": "geojson",
        "where": f"({span}) OR (starttime IS NULL AND {storm_span})",
        "geometry": json.dumps(geometry, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(DEFAULT_OUT_FIELDS),
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "2000",
    }
    return f"{DAT_DAMAGE_LINES_URL.rstrip('/')}/query?{urllib.parse.urlencode(params)}"


def fetch_dat_damage_paths(
    west: float, south: float, east: float, north: float, day: date, *,
    tolerance_days: int = _DATE_TOLERANCE_DAYS,
) -> dict:
    """DAT damage lines crossing the box within the day +/- tolerance_days.
    An empty collection when there are none; raises only when the request fails."""
    url = _arcgis_query_url(west, south, east, north, day, tolerance_days)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    payload = package_sources.read_url(
        "damage paths", request, urlopen, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT,
    ).decode("utf-8")
    fc = json.loads(payload)
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise RuntimeError("DAT query did not return a GeoJSON FeatureCollection")

    features = []
    for feature in fc.get("features", []) or []:
        if not isinstance(feature, dict):
            continue
        if not _feature_intersects_bbox(feature, west, south, east, north):
            continue
        if not _date_within_tolerance(feature, day, tolerance_days):
            continue
        features.append(_normalize_feature(feature, source="NOAA DAT"))
    return {"type": "FeatureCollection", "features": features}


def _ncei_details_url(year: int) -> str:
    """The newest StormEvents_details CSV for a year, from the folder listing."""
    base = NCEI_EVENT_CSV_BASE_URL.rstrip("/")
    request = Request(base + "/", headers={"User-Agent": _USER_AGENT})
    html = package_sources.read_url(
        "damage paths", request, urlopen, timeout=_NCEI_TIMEOUT, context=config.NSSL_SSL_CONTEXT,
    ).decode("utf-8", errors="replace")
    pattern = re.compile(rf"StormEvents_details-ftp_v1\.0_d{int(year)}_c(\d{{8}})\.csv\.gz")
    matches = sorted(set(pattern.findall(html)))
    if not matches:
        raise FileNotFoundError(f"No NCEI StormEvents details CSV found for {year}")
    return f"{base}/StormEvents_details-ftp_v1.0_d{int(year)}_c{matches[-1]}.csv.gz"


def _ncei_timezone_offset_hours(row: dict) -> float | None:
    for col in ("CZ_TIMEZONE", "cz_timezone"):
        label = (row.get(col) or "").strip().upper()
        if label:
            compact = re.sub(r"[^A-Z0-9+\-]", "", label)
            if compact in NCEI_TIMEZONE_OFFSETS_HOURS:
                return NCEI_TIMEZONE_OFFSETS_HOURS[compact]
            match = re.search(r"(?:UTC|GMT)?([+\-]\d{1,2})(?::?(\d{2}))?$", compact)
            if match:
                hours = float(match.group(1))
                minutes = float(match.group(2) or 0) / 60.0
                return hours + (minutes if hours >= 0 else -minutes)
    return None


def _localize_ncei_time(value: str, row: dict) -> datetime | None:
    """A Storm Events local time ("17-NOV-13 13:14:00") in UTC, using
    CZ_TIMEZONE's fixed offset; taken as UTC when the zone is missing."""
    if not value:
        return None
    naive = None
    for fmt in ("%d-%b-%y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(value.strip(), fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return None
    offset = _ncei_timezone_offset_hours(row)
    if offset is None:
        return naive.replace(tzinfo=timezone.utc)
    return (naive - timedelta(hours=offset)).replace(tzinfo=timezone.utc)


def _ncei_row_to_feature(row: dict) -> dict | None:
    begin_lat, begin_lon = _to_float(row.get("BEGIN_LAT")), _to_float(row.get("BEGIN_LON"))
    if begin_lat is None or begin_lon is None:
        return None
    end_lat, end_lon = _to_float(row.get("END_LAT")), _to_float(row.get("END_LON"))
    if end_lat is None or end_lon is None:
        end_lat, end_lon = begin_lat, begin_lon

    begin_t = _localize_ncei_time(row.get("BEGIN_DATE_TIME", ""), row)
    end_t = _localize_ncei_time(row.get("END_DATE_TIME", ""), row)
    props = {
        "event_id": (row.get("EVENT_ID") or "").strip() or None,
        "wfo": row.get("WFO"),
        "efscale": row.get("TOR_F_SCALE") or row.get("MAGNITUDE"),
        "efnum": None,
        "length": _to_float(row.get("TOR_LENGTH")),
        "width": _to_float(row.get("TOR_WIDTH")),
        "injuries": _to_float(row.get("INJURIES_DIRECT")),
        "fatalities": _to_float(row.get("DEATHS_DIRECT")),
        "maxwind": None,
        "comments": row.get("EVENT_NARRATIVE") or row.get("EPISODE_NARRATIVE"),
    }
    if begin_t is not None:
        props["starttime"] = begin_t.timestamp() * 1000.0
    if end_t is not None:
        props["endtime"] = end_t.timestamp() * 1000.0

    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[begin_lon, begin_lat], [end_lon, end_lat]]},
        "properties": props,
    }
    return _normalize_feature(feature, source="NCEI Storm Events")


def _ncei_tornadoes(year: int) -> list[dict]:
    """Every tornado in a year's Storm Events CSV, read once per run."""
    with _ncei_lock:
        cached = _ncei_year_cache.get(year)
        if cached is not None:
            return cached
        url = _ncei_details_url(year)
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        raw = package_sources.read_url(
            "damage paths", request, urlopen, timeout=_NCEI_TIMEOUT, context=config.NSSL_SSL_CONTEXT,
        )
        text = gzip.decompress(raw).decode("utf-8", errors="replace")
        features = []
        for row in csv.DictReader(io.StringIO(text)):
            if (row.get("EVENT_TYPE") or "").strip().upper() != "TORNADO":
                continue
            feature = _ncei_row_to_feature(row)
            if feature is not None:
                features.append(feature)
        _ncei_year_cache[year] = features
        return features


def fetch_ncei_storm_events_damage_paths(
    west: float, south: float, east: float, north: float, day: date, *,
    tolerance_days: int = _DATE_TOLERANCE_DAYS,
) -> dict:
    """Tornado paths (begin to end point) from NCEI Storm Events, for when
    DAT has nothing for the box and date."""
    lo = day - timedelta(days=tolerance_days)
    hi = day + timedelta(days=tolerance_days)
    features = []
    for year in range(lo.year, hi.year + 1):
        try:
            year_features = _ncei_tornadoes(year)
        except FileNotFoundError as exc:
            log.warning("NCEI Storm Events: %s", exc)
            continue
        for feature in year_features:
            if _feature_intersects_bbox(feature, west, south, east, north) \
                    and _date_within_tolerance(feature, day, tolerance_days):
                features.append(feature)
    return {"type": "FeatureCollection", "features": features}


class ArchiveDamagePathsFetcher(QObject):
    """Loads the damage paths inside a drawn box on the session's date, in
    the background. A newer box abandons the one still loading."""

    paths_ready = pyqtSignal(dict)  # normalized GeoJSON FeatureCollection
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._session_date = session_date
        self._generation = 0

    def set_bbox(self, west: float, south: float, east: float, north: float) -> None:
        self._generation += 1
        threading.Thread(
            target=self._load, args=(self._generation, west, south, east, north),
            daemon=True, name="damage-paths",
        ).start()

    def cancel(self) -> None:
        """Drop whatever is loading."""
        self._generation += 1

    def _current(self, generation: int) -> bool:
        return generation == self._generation

    def _load(self, generation: int, west: float, south: float, east: float, north: float) -> None:
        day = self._session_date.date()
        try:
            dat_fc = fetch_dat_damage_paths(west, south, east, north, day)
        except Exception as exc:  # noqa: BLE001
            log.warning("Damage paths: DAT query failed: %s", exc)
            dat_fc = dict(EMPTY_FC)
        if not self._current(generation):
            return
        if dat_fc.get("features"):
            emit_alive(self, "paths_ready", dat_fc)
            return

        try:
            ncei_fc = fetch_ncei_storm_events_damage_paths(west, south, east, north, day)
        except Exception as exc:  # noqa: BLE001
            log.warning("Damage paths: NCEI Storm Events fallback failed: %s", exc)
            if self._current(generation):
                emit_alive(self, "error", f"Damage paths unavailable: {exc}")
            return
        if self._current(generation):
            emit_alive(self, "paths_ready", ncei_fc)
