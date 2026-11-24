"""One-second FOFS mobile-mesonet observations for archive playback: the
NSSL provider's vehicle-obs source (FofsVehicleObsSource), with the repairs
the published FOFS files need. Playback and the cleanup every source gets
are generic (archive/fetchers/vehicle_obs_archive_fetcher.py,
archive/vehicle_obs.py)."""

from __future__ import annotations

import csv
import io
import logging
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request

from data import auth
import providers.nssl as nssl
from providers.nssl.archive.fofs_known_corrections import KNOWN_CORRECTIONS
from archive.vehicle_obs import (
    _MAX_SPEED_KMS, PHYSICAL_LIMITS, SAME_TRACK_KM as _SAME_TRACK_KM, clean_value,
    float_or_none as _float_or_none, km as _km, nearest_fix, one_fix_per_second as _one_fix_per_second,
    plausible_dewpoint, valid_position as _valid_position,
)
from providers.nssl.archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS, mqtt_vehicle_id, thredds_vehicle_id
from core.observation import Observation
from core import package_sources, provenance
from providers.nssl import paths

__all__ = ["FofsVehicleObsSource", "nearest_fix", "parse_vehicle_csv"]

log = logging.getLogger(__name__)

_FILE_ROOT = paths.file_url(paths.FOFS_MESONET_DATA)
_USER_AGENT = "Mozilla/5.0 STORM/1.0"


# data.nssl.noaa.gov sits behind a reverse proxy/WAF (duplicate security
# headers, a session cookie, and a 410 for requests without a plausible
# User-Agent, hence _USER_AGENT). Under a burst of requests it has been
# observed to soft-throttle with connection timeouts rather than a clean 429,
# then recover on its own within minutes once traffic quiets down, so NSSL
# files are fetched with auth.urlopen_with_retry.


def _daily_url(vehicle_id: str, date_str: str) -> str:
    directory = thredds_vehicle_id(vehicle_id)
    return f"{_FILE_ROOT}/{directory}/raw/{date_str}.txt"


# FOFS columns -> the Observation fields their limits are checked as
# (archive/vehicle_obs.py PHYSICAL_LIMITS)
_FOFS_FIELDS = {
    "t_fast": "temperature_c",
    "dewpoint": "dewpoint_c",
    "sfc_wspd": "wind_speed_ms",
    "sfc_wdir": "wind_dir_deg",
    "pressure": "pressure_mb",
    "compass_dir": "heading_deg",
}
assert set(_FOFS_FIELDS.values()) <= set(PHYSICAL_LIMITS)

# Some files carry relative humidity under the dewpoint header (probe1,
# Aug 2020-2021: "dewpoint" equal to rh_slow row after row). When most rows
# match like that, the whole column is humidity, including values that
# would pass as plausible dewpoints, so none of it is used.
_RH_AS_DEWPOINT_TOLERANCE = 1.5
_RH_AS_DEWPOINT_FRACTION = 0.8


def _measurement(row: dict, column: str) -> float | None:
    return clean_value(_FOFS_FIELDS[column], row.get(column))


def _dewpoint(row: dict) -> float | None:
    temperatures = [t for t in (_float_or_none(row.get("t_slow")), _measurement(row, "t_fast"))
                    if t is not None and math.isfinite(t) and t > -900.0]
    return plausible_dewpoint(_measurement(row, "dewpoint"), temperatures)


def _dewpoint_column_is_humidity(rows: list[dict]) -> bool:
    pairs = [(_float_or_none(r.get("dewpoint")), _float_or_none(r.get("rh_slow"))) for r in rows]
    pairs = [(d, h) for d, h in pairs if d is not None and h is not None and h > 0]
    matching = sum(1 for d, h in pairs if abs(d - h) <= _RH_AS_DEWPOINT_TOLERANCE)
    return bool(pairs) and matching >= _RH_AS_DEWPOINT_FRACTION * len(pairs)


# How gps_date strings in FOFS raw/*.txt files relate to the real UTC day.
#
# The mobile-mesonet CR6 logger records the receiver's own $GPRMC date as
# DDMMYY (FOFS/Mobile-Mesonet/logger/NSSL_MobileMesonet_CR6_loggerCode.CR6),
# and the original logger files carry correct dates and times throughout --
# confirmed 2026-09-24 against every locally available probe1/probe2 logger
# file from 2017 and LIFT 2024 (Probe_N_MMDDYYYY_QC_all.dat and the like).
# The damage happens later, in the step that converts those logger files
# into the published THREDDS CSVs:
#   * gps_date is sometimes rewritten as a garbled string, a pure function
#     of the true day (_thredds_garbled_gps_date), while gps_time stays
#     exact to the second (cross-checked against dltruck's independent GPS
#     track: same place at the same second);
#   * an MMDDYYYY logger filename is sometimes read as DDMMYYYY, filing a
#     day's data under its day/month swap (_swapped_file_date), e.g.
#     2024-05-06's data published as raw/20240605.txt;
#   * rows from 00:00:00-00:09:59 UTC are dropped (not recoverable here).
# Older "QC_met" logger files (2019-2023) carry only a decimal hour, no
# date, so the conversion took the date from the logger file's name and
# wrote it as MMDDYY. Those dates are only as good as that name reading:
# right for 2019's YYMMDD names, wrong for 2023's MMDDYYYY names whenever
# they could be read as DDMMYYYY -- which produced swapped-name duplicates
# of days that were also published correctly (_is_misfiled_duplicate).
#
# The processed/*.nc files come out of the same conversion -- their
# epochtime is NaN for garbled days and confidently wrong for swapped ones
# -- so they can't be used to check any of this and aren't read at all.
#
# Rather than guess per row, a whole file is resolved at once: each
# hypothesis (which day the file really starts on x which encoding it
# uses) predicts every row's exact gps_date string, and the one that
# predicts the most rows wins. Once the THREDDS files are republished
# correctly, they simply match the plain DDMMYY hypothesis.

def _ddmmyy(day: date) -> str:
    return day.strftime("%d%m%y")


def _mmddyy(day: date) -> str:
    return day.strftime("%m%d%y")


def _thredds_garbled_gps_date(day: date) -> str:
    """The gps_date the THREDDS conversion wrote for `day` when it garbled it.

    Derived from every raw-logger/published-CSV pair available (LIFT 2024
    Apr-Jun and 2017, ~1M rows): e.g. 27 Apr 2024 -> "020724", 30 Apr 2024
    -> "020404", 1 May 2024 -> "100524". Days 2-12 come through unchanged.
    Not every file was garbled, so this is one hypothesis among several,
    never assumed.
    """
    mm, yy = f"{day.month:02d}", f"{day.year % 100:02d}"
    if day.day == 1:
        return f"10{mm}{yy}"
    if day.day <= 12:
        return f"{day.day:02d}{mm}{yy}"
    tens, units = divmod(day.day, 10)
    if units == 0:
        return f"0204{mm}"
    return f"0{tens}0{units}{yy}"


# Order breaks ties between hypotheses that explain the same rows.
_GPS_DATE_ENCODINGS = (
    ("DDMMYY", _ddmmyy),
    ("THREDDS-garbled", _thredds_garbled_gps_date),
    ("MMDDYY", _mmddyy),
)

# A backwards jump in gps_time bigger than this, with gps_date changing
# too (true of every encoding above), is a UTC midnight rollover. The date
# condition keeps no-fix rows at 000000 (probe1's raw/20110828.txt) from
# counting as one.
_ROLLOVER_BACKSTEP_S = 12 * 3600


def _swapped_file_date(day: date) -> date | None:
    """The day/month swap of `day`, if one exists. Self-inverse: a file
    named for D holds data from _swapped_file_date(D) when the swap bug
    hit it, and D's own data may be filed under _swapped_file_date(D)."""
    if day.day > 12 or day.day == day.month:
        return None
    return date(day.year, day.day, day.month)


@dataclass(frozen=True)
class _FileDating:
    start_day: date  # the real UTC day of the file's first row
    encoding: str
    matched: int
    total: int


def _resolve_row_days(
    stamps: list[tuple[str, int]],
    file_date: date,
) -> tuple[list[date | None], _FileDating | None]:
    """Assign the real UTC day to each (gps_date, seconds-of-day) row of one
    file, or None where a row's gps_date contradicts the file's resolved
    dating (e.g. a no-fix row still carrying the receiver's default date).
    A file whose best hypothesis explains no more than half its rows is
    rejected entirely rather than trusted on thin evidence."""
    offsets: list[int] = []
    rollovers = 0
    previous: tuple[str, int] | None = None
    for gps_date, seconds in stamps:
        if (
            previous is not None
            and gps_date != previous[0]
            and seconds < previous[1] - _ROLLOVER_BACKSTEP_S
        ):
            rollovers += 1
        offsets.append(rollovers)
        previous = (gps_date, seconds)

    start_days = [file_date]
    swapped = _swapped_file_date(file_date)
    if swapped is not None:
        start_days.append(swapped)

    best: tuple[int, date, str, Callable[[date], str]] | None = None
    for start_day in start_days:
        for name, encode in _GPS_DATE_ENCODINGS:
            expected = {k: encode(start_day + timedelta(days=k)) for k in set(offsets)}
            matched = sum(
                1 for (gps_date, _), k in zip(stamps, offsets) if gps_date == expected[k]
            )
            if best is None or matched > best[0]:
                best = (matched, start_day, name, encode)

    if best is None or best[0] * 2 <= len(stamps):
        return [None] * len(stamps), None
    matched, start_day, name, encode = best
    days: list[date | None] = []
    for (gps_date, _), k in zip(stamps, offsets):
        day = start_day + timedelta(days=k)
        days.append(day if gps_date == encode(day) else None)
    return days, _FileDating(start_day, name, matched, len(stamps))


def parse_vehicle_csv(
    text: str,
    vehicle_id: str,
    icon_type: str | None = None,
    *,
    file_date: date,
) -> list[Observation]:
    """Parse one FOFS raw/<file_date>.txt file into timestamp-sorted
    observations, dated per the module notes above. The result can span
    two UTC days (files follow the crew's local day); callers filter."""
    return _one_fix_per_second(_parse_vehicle_file(text, vehicle_id, icon_type, file_date)[0])[0]


def _parse_vehicle_file(
    text: str,
    vehicle_id: str,
    icon_type: str | None,
    file_date: date,
) -> tuple[list[Observation], _FileDating | None]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            gps_date = row["gps_date"].strip().zfill(6)
            gps_time = row["gps_time"].strip().zfill(6)
            hour, minute, second = int(gps_time[:2]), int(gps_time[2:4]), int(gps_time[4:])
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
        if len(gps_time) != 6 or hour > 23 or minute > 59 or second > 59:
            continue
        if not _valid_position(lat, lon):
            continue
        rows.append((gps_date, hour * 3600 + minute * 60 + second, lat, lon, row))

    days, dating = _resolve_row_days([(r[0], r[1]) for r in rows], file_date)
    if dating is not None and (dating.start_day != file_date or dating.encoding != "DDMMYY"):
        log.info(
            "FOFS %s raw/%s.txt: dated as %s starting %s (%d/%d rows) -- see the "
            "gps_date notes in vehicle_obs_archive_fetcher",
            vehicle_id, file_date.strftime("%Y%m%d"), dating.encoding,
            dating.start_day.isoformat(), dating.matched, dating.total,
        )

    humidity_as_dewpoint = _dewpoint_column_is_humidity([r[4] for r in rows])
    if humidity_as_dewpoint:
        log.info("FOFS %s raw/%s.txt: dewpoint column holds relative humidity; not used",
                 vehicle_id, file_date.strftime("%Y%m%d"))

    observations: list[Observation] = []
    for (_, seconds, lat, lon, row), day in zip(rows, days):
        if day is None:
            continue
        observations.append(Observation(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
            icon_type=icon_type,
            temperature_c=_measurement(row, "t_fast"),
            dewpoint_c=None if humidity_as_dewpoint else _dewpoint(row),
            wind_speed_ms=_measurement(row, "sfc_wspd"),
            wind_dir_deg=_measurement(row, "sfc_wdir"),
            pressure_mb=_measurement(row, "pressure"),
            heading_deg=_measurement(row, "compass_dir"),
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations, dating


@dataclass(frozen=True)
class _Correction:
    first: datetime
    last: datetime
    lat: float
    lon: float
    shift_days: int | None
    end_lat: float | None = None
    end_lon: float | None = None


_CORRECTIONS: dict[tuple[str, date], list[_Correction]] = {}
for _vehicle, _file, _first, _last, _lat, _lon, _shift, *_end in KNOWN_CORRECTIONS:
    _day = datetime.strptime(_file, "%Y%m%d").replace(tzinfo=timezone.utc)
    _CORRECTIONS.setdefault((_vehicle, _day.date()), []).append(_Correction(
        _day + timedelta(hours=int(_first[:2]), minutes=int(_first[3:5]), seconds=int(_first[6:])),
        _day + timedelta(hours=int(_last[:2]), minutes=int(_last[3:5]), seconds=int(_last[6:])),
        _lat, _lon, _shift, *_end,
    ))


def _follow_track(observations: list[Observation], correction: _Correction) -> set[int]:
    """Indexes of the rows forming the corrected stretch.

    Files interleave several tracks at the same timestamps, sometimes
    parked metres apart at the same spot, so the stretch is chosen as a
    whole: one row per second, every step physically possible, starting
    at the row at the entry's first fix, ending nearest its last fix when
    the entry gives one, and otherwise travelling the least total distance.
    """
    by_second: dict[datetime, list[tuple[int, Observation]]] = {}
    for i, obs in enumerate(observations):
        if correction.first <= obs.timestamp <= correction.last:
            by_second.setdefault(obs.timestamp, []).append((i, obs))
    seconds = sorted(by_second)
    if not seconds or seconds[0] != correction.first:
        return set()

    start = Observation(None, correction.lat, correction.lon, correction.first)
    first_i, first_obs = min(by_second[seconds[0]], key=lambda item: _km(start, item[1]))
    if _km(start, first_obs) > _SAME_TRACK_KM:
        return set()
    # layer: list of (distance so far, row index, obs, back-pointer into previous layer)
    layers = [[(0.0, first_i, first_obs, None)]]
    for second in seconds[1:]:
        previous = layers[-1]
        layer = []
        for i, obs in by_second[second]:
            best = None
            for k, (cost, _, prev_obs, _) in enumerate(previous):
                gap = (obs.timestamp - prev_obs.timestamp).total_seconds()
                step = _km(prev_obs, obs)
                if step <= _SAME_TRACK_KM + _MAX_SPEED_KMS * gap and (best is None or cost + step < best[0]):
                    best = (cost + step, k)
            if best is not None:
                layer.append((best[0], i, obs, best[1]))
        if layer:
            layers.append(layer)

    end = (Observation(None, correction.end_lat, correction.end_lon, correction.last)
           if correction.end_lat is not None else None)
    k = min(range(len(layers[-1])),
            key=lambda n: (_km(end, layers[-1][n][2]) if end else 0.0, layers[-1][n][0]))
    chosen: set[int] = set()
    for layer in reversed(layers):
        _, i, _, back = layer[k]
        chosen.add(i)
        k = back if back is not None else 0
    return chosen


def _apply_known_corrections(
    thredds_vehicle: str,
    file_date: date,
    observations: list[Observation],
) -> list[Observation]:
    """Move (or drop) the stretches of one file listed in
    providers/nssl/archive/fofs_known_corrections.py."""
    corrections = _CORRECTIONS.get((thredds_vehicle, file_date))
    if not corrections:
        return observations
    shifts: dict[int, int | None] = {}
    for correction in corrections:
        for i in _follow_track(observations, correction):
            shifts[i] = correction.shift_days
    corrected: list[Observation] = []
    for i, obs in enumerate(observations):
        if i not in shifts:
            corrected.append(obs)
        elif shifts[i] is not None:
            corrected.append(_with_timestamp(obs, obs.timestamp + timedelta(days=shifts[i])))
    log.info("FOFS %s raw/%s.txt: %d rows re-dated or dropped per known corrections",
             thredds_vehicle, f"{file_date:%Y%m%d}", len(shifts))
    return corrected


def _with_timestamp(obs: Observation, timestamp: datetime) -> Observation:
    return Observation(
        vehicle_id=obs.vehicle_id, lat=obs.lat, lon=obs.lon, timestamp=timestamp,
        icon_type=obs.icon_type, temperature_c=obs.temperature_c, dewpoint_c=obs.dewpoint_c,
        wind_speed_ms=obs.wind_speed_ms, wind_dir_deg=obs.wind_dir_deg, pressure_mb=obs.pressure_mb,
    )


def _candidate_file_dates(session_day: date, thredds_vehicle: str | None = None) -> list[date]:
    """Every raw/<date>.txt that can hold rows from session_day: the day's
    own file and its neighbors (local-day files run past 00 UTC, and 2010
    files hold stretches of the adjacent days), the day/month-swapped names
    any of them could have been published under, and any file a known
    correction moves rows out of onto these days."""
    candidates: list[date] = []
    window = [session_day + timedelta(days=k) for k in (-1, 0, 1)]
    for day in window:
        for candidate in (day, _swapped_file_date(day)):
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
    if thredds_vehicle is not None:
        for (vehicle, file_date), corrections in sorted(_CORRECTIONS.items()):
            if vehicle != thredds_vehicle or file_date in candidates:
                continue
            if any(file_date + timedelta(days=c.shift_days + k) in window
                   for c in corrections if c.shift_days is not None for k in (-1, 0, 1)):
                candidates.append(file_date)
    return candidates


class FofsVehicleObsSource:
    """The FOFS mesonet files on NSSL THREDDS (archive/vehicle_obs.py's
    VehicleObsSource), by MQTT vehicle ID; the files are under the THREDDS
    names (vehicle_aliases.py)."""

    name = "NSSL FOFS mesonet"

    def vehicles(self, session_day: date) -> set[str] | None:
        """Every vehicle THREDDS has files for this session (MQTT IDs). One
        from a fixed list misses new vehicle folders; without an index, the
        known platforms are probed instead."""
        published = available_vehicles(datetime(session_day.year, session_day.month,
                                                session_day.day, tzinfo=timezone.utc))
        if published is None:
            published = set(KNOWN_FOFS_PLATFORMS)
        return {mqtt_vehicle_id(v) for v in published}

    @staticmethod
    def same_vehicle(a: str, b: str) -> bool:
        return thredds_vehicle_id(a) == thredds_vehicle_id(b)

    @staticmethod
    def calendar_platforms() -> list:
        """The FOFS vehicles on the archive calendar (providers/nssl/archive/catalog.py)."""
        from providers.nssl.archive.catalog import FOFS_PLATFORMS
        return list(FOFS_PLATFORMS)

    def load(self, vehicle_id: str, icon_type: str | None, session_day: date) -> list[Observation] | None:
        """Collect this session's rows from every file that can hold
        them (see _candidate_file_dates), after known corrections. Missing
        candidates cost one fast 404 each. Rows found in more than one file
        (THREDDS has some days under both their real and swapped names)
        are kept once by the caller's cleanup, where rows disagree about
        where the vehicle was at a second the continuous track wins."""
        files: dict[date, tuple[list[Observation], _FileDating | None]] = {}

        def fetch(file_date: date) -> tuple[list[Observation], _FileDating | None]:
            if file_date not in files:
                files[file_date] = self._fetch_vehicle_file(vehicle_id, icon_type, file_date)
            return files[file_date]

        thredds_vehicle = thredds_vehicle_id(vehicle_id)
        # the session's UTC day and its next morning (archive/session.py)
        days = (session_day, session_day + timedelta(days=1))
        # Download every candidate file at once rather than one after
        # another -- a session commonly needs three or four, and each can
        # take many seconds from THREDDS during a busy archive startup.
        candidates = list(dict.fromkeys(d for day in days for d in _candidate_file_dates(day, thredds_vehicle)))
        with ThreadPoolExecutor(max_workers=4) as pool:
            for file_date, result in zip(candidates, pool.map(
                    lambda d: self._fetch_vehicle_file(vehicle_id, icon_type, d), candidates)):
                files[file_date] = result

        collected: list[Observation] = []
        sources: list[str] = []
        for day in days:
            for file_date in _candidate_file_dates(day, thredds_vehicle):
                observations, dating = fetch(file_date)
                if dating is not None and _is_misfiled_duplicate(file_date, observations, dating, fetch):
                    log.info(
                        "FOFS %s raw/%s.txt: skipped -- a copy of raw/%s.txt filed under a "
                        "misread date (see the gps_date notes in vehicle_obs_archive_fetcher)",
                        vehicle_id, f"{file_date:%Y%m%d}", f"{_swapped_file_date(file_date):%Y%m%d}",
                    )
                    continue
                observations = _apply_known_corrections(thredds_vehicle, file_date, observations)
                in_day = [obs for obs in observations if obs.timestamp.date() == day]
                if in_day:
                    collected += in_day
                    sources.append(f"{file_date:%Y%m%d}.txt ({len(in_day)})")
        log.debug("FOFS %s on %s: rows from %s", vehicle_id, f"{session_day:%Y%m%d}",
                  ", ".join(sources) or "no files")
        collected.sort(key=lambda obs: obs.timestamp)
        return collected

    def _fetch_vehicle_file(
        self,
        vehicle_id: str,
        icon_type: str | None,
        file_date: date,
    ) -> tuple[list[Observation], _FileDating | None]:
        collected: list[Observation] = []
        best: _FileDating | None = None
        for url in _file_urls(vehicle_id, file_date):
            observations, dating = self._fetch_one_file(url, vehicle_id, icon_type, file_date)
            collected += observations
            if dating is not None and (best is None or dating.matched > best.matched):
                best = dating
        collected.sort(key=lambda obs: obs.timestamp)
        return collected, best

    def _fetch_one_file(
        self,
        url: str,
        vehicle_id: str,
        icon_type: str | None,
        file_date: date,
    ) -> tuple[list[Observation], _FileDating | None]:
        raw = package_sources.get(url)          # an opened case package's copy, if it has one
        if raw is None:
            request = Request(url, headers={"User-Agent": _USER_AGENT})
            try:
                with auth.urlopen_with_retry(request, timeout=30) as response:
                    raw = response.read()
            except HTTPError as exc:
                if exc.code in (404, 410):
                    return [], None
                raise
        text = raw.decode("utf-8", errors="replace")
        if text.strip() and not _is_published_format(text):
            # e.g. a _legacy_data original in its logger's own format -- the
            # same rows as the published copy, which is read instead
            log.debug("FOFS %s: %s is not in the published format; skipped", vehicle_id, url)
            return [], None
        observations, dating = _parse_vehicle_file(text, vehicle_id, icon_type, file_date)
        if observations:
            provenance.record("mesonet", url, raw)   # files with no rows for the day aren't part of the case
        if not observations and text.strip():
            # A real, non-empty file whose gps_date values fit neither its
            # own name nor any known encoding of it -- e.g. a receiver with
            # no fix still reporting a stale default date (probe1's
            # raw/20170511.txt is entirely Feb 2017 no-fix rows). Worth a
            # WARNING because "0 observations" alone looks like an empty
            # file, and telling them apart otherwise means inspecting the
            # file by hand.
            log.warning(
                "One-second archive: %s's raw CSV %s has data but no rows could be dated "
                "from it (gps_date matches neither the file's date nor any known encoding) "
                "-- likely no GPS fix, not a fetch failure: %s",
                vehicle_id, file_date.strftime("%Y%m%d"), url,
            )
        return observations, dating


def _is_published_format(text: str) -> bool:
    """Whether a file is in the standard published CSV layout STORM parses."""
    header = text.lstrip().split("\n", 1)[0].lower()
    return all(column in header for column in ("gps_date", "gps_time", "lat", "lon"))


def _file_urls(vehicle_id: str, file_date: date) -> list[str]:
    """Every published copy of a vehicle's file for one date, wherever the
    published index (providers/nssl/indexes.py FOFS) says it sits. _legacy_data copies
    are the pre-conversion originals of a published file and are only
    tried when no other copy exists. Without an index, the usual location."""
    from providers.nssl import indexes
    index = indexes.FOFS.get()
    if index is None:
        return [_daily_url(vehicle_id, file_date.strftime("%Y%m%d"))]
    files = index.files_for(thredds_vehicle_id(vehicle_id), file_date)
    current = [f for f in files if "/_legacy" not in f.path]
    return [f.url for f in (current or files)]


def available_vehicles(session_date: datetime) -> set[str] | None:
    """THREDDS vehicle names with a published file that can feed this
    session (its UTC day and the next morning), or None without an index."""
    from providers.nssl import indexes
    index = indexes.FOFS.get()
    if index is None:
        return None
    day = session_date.date()
    vehicles = set()
    for vehicle in index.platforms():
        dates = {d for k in (0, 1) for d in _candidate_file_dates(day + timedelta(days=k), vehicle)}
        if any(index.files_for(vehicle, d) for d in dates):
            vehicles.add(vehicle)
    return vehicles


def _is_misfiled_duplicate(
    file_date: date,
    observations: list[Observation],
    dating: _FileDating,
    fetch: Callable[[date], tuple[list[Observation], _FileDating | None]],
) -> bool:
    """Whether a file's dates were invented from a misread file name.

    MMDDYY dates came from the logger file's name, not the GPS, so a file
    named D with MMDDYY dates can't show by itself whether D was read
    right. It can be checked against its swap S: when raw/S.txt exists,
    carries GPS-derived dates for day S, and contains the same fixes at
    the same times of day, D is S's data under a misread date (as with
    probe1's raw/20230106.txt vs raw/20230601.txt). Without such a twin the
    file is trusted -- probe1's TORUS 2019 files are MMDDYY and correct.
    Positions are compared loosely because the two copies were written at
    different precisions (33.416 vs 33.41601); the misfiled 2023 copies
    also have their met columns scrambled, so they must not be shown.
    """
    swapped = _swapped_file_date(file_date)
    if dating.encoding != "MMDDYY" or swapped is None or not observations:
        return False
    twin_observations, twin_dating = fetch(swapped)
    if twin_dating is None or twin_dating.encoding == "MMDDYY" or twin_dating.start_day != swapped:
        return False

    twin_fixes: dict = {}
    for obs in twin_observations:
        if obs.timestamp.date() == swapped:
            twin_fixes.setdefault(obs.timestamp.time(), (obs.lat, obs.lon))
    first_day = [obs for obs in observations if obs.timestamp.date() == dating.start_day]
    shared = 0
    for obs in first_day:
        twin = twin_fixes.get(obs.timestamp.time())
        if twin is not None and abs(twin[0] - obs.lat) <= 1e-3 and abs(twin[1] - obs.lon) <= 1e-3:
            shared += 1
    return shared * 2 > len(first_day)

