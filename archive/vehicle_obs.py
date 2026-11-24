"""
Vehicle observations for ARCHIVE mode from logger files: one CSV per vehicle
per day, read with the ``[obs_columns]`` map (the one live obs-file watching
uses). Sources come from ``[archive] vehicle_obs`` in storm.toml, tried in
order for each vehicle; the first that has data for it wins:

    "vehicle-obs"            a folder (relative to the STORM folder)
    "https://host/obs"       a URL root
    "nssl"                   a provider's own files (providers/nssl/: FOFS, or a
                             code provider's vehicle_obs_source(), docs/providers.md)

Below a folder or URL root a file sits at ``[archive] vehicle_obs_path``
(default ``{vehicle}/{date}.csv``, date as YYYYMMDD). Without the setting:
the provider sources of the providers turned on. A vehicle no source has
moves on its recorded MQTT positions. The layout is in docs/archive-data.md.

Every source's rows get the same sanity checks (cleanup_track): missing-value
codes and impossible readings dropped, null-island fixes, impossible jumps
and frozen fixes beside them dropped, and one fix per second. Qt-free; the
playback is archive/fetchers/vehicle_obs_archive_fetcher.py.
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol
import settings
from archive import history
from archive.session import session_bounds
from core import package_sources
from core.geo import haversine_km
from core.obs_cleanup import (  # noqa: F401  (also used from here by the NSSL source)
    PHYSICAL_LIMITS, clean_value, float_or_none, plausible_dewpoint, valid_position,
)
from core.observation import Observation
from headless.storm_headless.obs_columns import FieldMap, row_time

log = logging.getLogger(__name__)

# a URL root's optional list of the days (and vehicles) it has
DATES_FILE = "dates.json"

# Consecutive-second fixes further apart than this aren't the same track.
SAME_TRACK_KM = 1.0
_MAX_SPEED_KMS = 0.09  # 90 m/s -- faster than any vehicle between fixes


def km(a: Observation, b: Observation) -> float:
    return haversine_km(a.lat, a.lon, b.lat, b.lon)


def one_fix_per_second(observations: list[Observation]) -> tuple[list[Observation], int]:
    """Collapse same-timestamp rows to one, following the vehicle's track.

    Some files interleave two tracks at identical timestamps (VORTEX2 2010:
    FOFS probe1's raw/20100524.txt repeats 23 May's last half hour, still
    moving, under 24 May's date). Keeping whichever row came first makes the
    vehicle flicker hundreds of km every second; instead each second keeps
    the row nearest the previous kept fix, starting from the first second
    that isn't ambiguous. Returns the kept rows and how many seconds had
    rows that disagreed on position.
    """
    groups: list[list[Observation]] = []
    for obs in observations:  # already sorted by timestamp
        if groups and groups[-1][0].timestamp == obs.timestamp:
            groups[-1].append(obs)
        else:
            groups.append([obs])
    if all(len(group) == 1 for group in groups):
        return observations, 0

    def ambiguous(group: list[Observation]) -> bool:
        return any(km(group[0], other) > SAME_TRACK_KM for other in group[1:])

    conflicts = sum(1 for group in groups if ambiguous(group))
    anchor = next((i for i, group in enumerate(groups) if not ambiguous(group)), 0)
    kept: list[Observation | None] = [None] * len(groups)
    kept[anchor] = groups[anchor][0]
    for i in range(anchor + 1, len(groups)):
        kept[i] = min(groups[i], key=lambda obs, prev=kept[i - 1]: km(prev, obs))
    for i in range(anchor - 1, -1, -1):
        kept[i] = min(groups[i], key=lambda obs, nxt=kept[i + 1]: km(nxt, obs))
    return kept, conflicts


# A stretch of at most this long that the track jumps to and straight back
# from, impossibly fast both ways, is a GPS glitch.
_EXCURSION_S = 60


def impossible_jump(a: Observation, b: Observation) -> bool:
    gap = max((b.timestamp - a.timestamp).total_seconds(), 1.0)
    distance = km(a, b)
    return distance >= SAME_TRACK_KM and distance / gap > _MAX_SPEED_KMS


def drop_excursions(observations: list[Observation]) -> tuple[list[Observation], int]:
    """Drop short stretches the fix jumped away to and straight back from."""
    kept: list[Observation] = []
    dropped = 0
    i = 0
    while i < len(observations):
        if kept and impossible_jump(kept[-1], observations[i]):
            j = i
            while (j < len(observations)
                   and (observations[j].timestamp - kept[-1].timestamp).total_seconds() <= _EXCURSION_S
                   and impossible_jump(kept[-1], observations[j])):
                j += 1
            if j < len(observations) and not impossible_jump(kept[-1], observations[j]):
                dropped += j - i
                i = j
                continue
        kept.append(observations[i])
        i += 1
    return kept, dropped


# A position that doesn't move at all right beside an impossible jump, while
# the other side of the jump is driving, is the receiver repeating its last
# fix before it has a new one. Only short frozen runs qualify, so a vehicle
# genuinely parked for a while is never touched.
_FROZEN_KM = 0.05
_STALE_MAX_S = 30 * 60
_CONTIGUOUS_S = 60


def _frozen_run(observations: list[Observation], index: int, step: int) -> list[int]:
    """Indexes of the unbroken run of rows at the same spot as
    observations[index], walking backwards (step -1) or forwards (+1)."""
    run = [index]
    j = index + step
    while 0 <= j < len(observations):
        gap = abs((observations[j].timestamp - observations[run[-1]].timestamp).total_seconds())
        if gap > _CONTIGUOUS_S or km(observations[index], observations[j]) > _FROZEN_KM:
            break
        run.append(j)
        j += step
    return run


def _moving_from(observations: list[Observation], index: int, step: int) -> bool:
    """Whether the vehicle is driving over the minute starting at index."""
    j = index
    while (0 <= j + step < len(observations)
           and abs((observations[j + step].timestamp - observations[index].timestamp).total_seconds()) <= 60):
        j += step
    return km(observations[index], observations[j]) > 10 * _FROZEN_KM


def drop_stale_fixes(observations: list[Observation]) -> tuple[list[Observation], int]:
    stale: set[int] = set()
    for i in range(len(observations) - 1):
        a, b = observations[i], observations[i + 1]
        if not impossible_jump(a, b):
            continue
        before, after = _frozen_run(observations, i, -1), _frozen_run(observations, i + 1, 1)
        for run, other_side, step in ((before, i + 1, 1), (after, i, -1)):
            duration = abs((observations[run[-1]].timestamp - observations[run[0]].timestamp).total_seconds())
            if duration <= _STALE_MAX_S and _moving_from(observations, other_side, step):
                stale.update(run)
                break
    return [obs for i, obs in enumerate(observations) if i not in stale], len(stale)


def cleanup_track(observations: list[Observation]) -> tuple[list[Observation], int, int]:
    """A vehicle's rows as one clean track: (kept, seconds with conflicting
    positions resolved, glitch fixes dropped)."""
    observations = sorted(observations, key=lambda obs: obs.timestamp)
    kept, conflicts = one_fix_per_second(observations)
    kept, glitches = drop_excursions(kept)
    kept, stale = drop_stale_fixes(kept)
    return kept, conflicts, glitches + stale


def nearest_fix(observations: list[Observation], when: datetime,
                within_s: float = 60.0) -> Observation | None:
    """The time-ordered track's fix nearest `when`, if within `within_s`."""
    times = [obs.timestamp for obs in observations]
    i = bisect.bisect_left(times, when)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(observations)]
    if not candidates:
        return None
    best = min(candidates, key=lambda j: abs((times[j] - when).total_seconds()))
    return observations[best] if abs((times[best] - when).total_seconds()) <= within_s else None


def parse_obs_csv(text: str, vehicle_id: str, icon_type: str | None,
                  fields: FieldMap) -> list[Observation]:
    """One logger CSV read with a column map, time-ordered. Rows without a
    readable time or a real position are skipped; readings that are
    missing-value codes or impossible are left empty."""
    observations = []
    for row in csv.DictReader(io.StringIO(text)):
        when = row_time(row, fields)
        lat, lon = float_or_none(row.get(fields.lat)), float_or_none(row.get(fields.lon))
        if when is None or lat is None or lon is None or not valid_position(lat, lon):
            continue

        def value(field, column):
            return clean_value(field, fields.to_storm(field, float_or_none(row.get(column)))) if column else None

        temperature = value("temperature_c", fields.temperature_c)
        observations.append(Observation(
            vehicle_id=vehicle_id, lat=lat, lon=lon, timestamp=when, icon_type=icon_type,
            temperature_c=temperature,
            dewpoint_c=plausible_dewpoint(value("dewpoint_c", fields.dewpoint_c), [temperature]),
            wind_speed_ms=value("wind_speed_ms", fields.wind_speed_ms),
            wind_dir_deg=value("wind_dir_deg", fields.wind_dir_deg),
            pressure_mb=value("pressure_mb", fields.pressure_mb),
            heading_deg=value("heading_deg", fields.heading_deg),
        ))
    observations.sort(key=lambda obs: obs.timestamp)
    return observations


# -- sources -------------------------------------------------------------------

class VehicleObsSource(Protocol):
    name: str

    def vehicles(self, session_day: date) -> set[str] | None:
        """Vehicle IDs this source has files for this session, or None when
        it can't tell (then only the recorded MQTT roster is asked)."""

    def load(self, vehicle_id: str, icon_type: str | None, session_day: date) -> list[Observation] | None:
        """The vehicle's rows that can fall in this session (cleaned up and
        cut to the session by the caller), or None when the source has no
        file for it. Raises when the source couldn't be read."""


def file_dates(session_day: date) -> list[date]:
    """The file dates that can hold a session's rows: its UTC day and the
    next morning, and the day before, for loggers that name files by the
    crew's local day (a US evening runs past 00Z)."""
    return [session_day + timedelta(days=k) for k in (-1, 0, 1)]


def in_session(observations: list[Observation], session_day: date) -> list[Observation]:
    start, cap = session_bounds(datetime(session_day.year, session_day.month, session_day.day))
    return [obs for obs in observations if start <= obs.timestamp <= cap]


def _vehicle_pattern(path_pattern: str, date_str: str) -> re.Pattern:
    """A regex matching the pattern's file for one date, capturing the vehicle."""
    head, _, tail = path_pattern.partition("{vehicle}")
    parts = [re.escape(p.replace("{date}", date_str)) for p in (head, tail)]
    return re.compile(parts[0] + r"([^/]+?)" + parts[1] + r"\Z")


class _FileSource:
    """Shared by the folder and URL sources: files at path_pattern below a root."""

    def __init__(self, path_pattern: str, fields: FieldMap):
        self.path_pattern = path_pattern
        self.fields = fields

    def relative(self, vehicle_id: str, file_date: date) -> str:
        vehicle = settings.SETTINGS.archive.file_vehicle(vehicle_id)
        return self.path_pattern.format(vehicle=vehicle, date=file_date.strftime("%Y%m%d"))

    def read(self, relative: str) -> Optional[bytes]:
        raise NotImplementedError

    def load(self, vehicle_id: str, icon_type: str | None, session_day: date) -> list[Observation] | None:
        found = False
        observations: list[Observation] = []
        for file_date in file_dates(session_day):
            relative = self.relative(vehicle_id, file_date)
            raw = self.read(relative)
            if raw is None:
                continue
            found = True
            rows = parse_obs_csv(raw.decode("utf-8", errors="replace"), vehicle_id, icon_type, self.fields)
            if not rows and raw.strip():
                log.warning("Vehicle obs: %s %s has rows but none with a readable time and position "
                            "under [obs_columns]", self.name, relative)
            observations += rows
        return observations if found else None


class FolderVehicleObsSource(_FileSource):
    def __init__(self, folder: Path, path_pattern: str, fields: FieldMap):
        super().__init__(path_pattern, fields)
        self.folder = folder
        self.name = f"folder {folder}"

    def read(self, relative: str) -> Optional[bytes]:
        path = self.folder / relative
        data = package_sources.get_local(path, relative)
        if data is None and path.is_file():
            data = package_sources.read_url("mesonet", path.resolve().as_uri(),
                                            lambda _url, _path=path: _path.open("rb"))
        return data

    def vehicles(self, session_day: date) -> set[str] | None:
        if not self.folder.is_dir():
            return set()
        found = set()
        for file_date in file_dates(session_day):
            date_str = file_date.strftime("%Y%m%d")
            regex = _vehicle_pattern(self.path_pattern, date_str)
            glob = self.path_pattern.replace("{vehicle}", "*").replace("{date}", date_str)
            for path in self.folder.glob(glob):
                match = regex.match(path.relative_to(self.folder).as_posix())
                if match and path.is_file():
                    found.add(settings.SETTINGS.archive.broker_vehicle(match[1]))
        return found

    def dates(self) -> dict[str, set[date]]:
        """Every vehicle's file dates, from the folder's names (the calendar)."""
        out: dict[str, set[date]] = {}
        if not self.folder.is_dir():
            return out
        regex = re.compile(re.escape(self.path_pattern)
                           .replace(re.escape("{vehicle}"), r"(?P<vehicle>[^/]+?)")
                           .replace(re.escape("{date}"), r"(?P<date>\d{8})") + r"\Z")
        glob = self.path_pattern.replace("{vehicle}", "*").replace("{date}", "[0-9]" * 8)
        for path in self.folder.glob(glob):
            match = regex.match(path.relative_to(self.folder).as_posix())
            if not match:
                continue
            try:
                day = datetime.strptime(match["date"], "%Y%m%d").date()
            except ValueError:
                continue
            out.setdefault(settings.SETTINGS.archive.broker_vehicle(match["vehicle"]), set()).add(day)
        return out


def fetch_url(url: str) -> Optional[bytes]:
    """A vehicle-obs file over HTTP(S); None on 404."""
    return history.fetch_bytes(url, "mesonet", timeout=30)


def parse_dates_file(text: str) -> tuple[set[date], dict[str, set[date]]]:
    """A root's dates.json: {"dates": [...], "vehicles": {"<id>": [...]}},
    both optional, days as YYYYMMDD. Unreadable days are skipped; a file
    that isn't such an object raises ValueError."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{DATES_FILE} must be a JSON object")

    def days(raw) -> set[date]:
        if not isinstance(raw, list):
            raise ValueError(f"{DATES_FILE}: dates must be lists of YYYYMMDD strings")
        out = set()
        for item in raw:
            try:
                out.add(datetime.strptime(str(item), "%Y%m%d").date())
            except ValueError:
                pass
        return out

    vehicles = payload.get("vehicles", {})
    if not isinstance(vehicles, dict):
        raise ValueError(f"{DATES_FILE}: vehicles must map vehicle IDs to dates")
    return days(payload.get("dates", [])), {str(k): days(v) for k, v in vehicles.items()}


class UrlVehicleObsSource(_FileSource):
    def __init__(self, root: str, path_pattern: str, fields: FieldMap):
        super().__init__(path_pattern, fields)
        self.root = root.rstrip("/")
        self.name = f"URL {self.root}"
        self._listing: tuple[set[date], dict[str, set[date]]] | None = None
        self._listing_read = False

    def read(self, relative: str) -> Optional[bytes]:
        return fetch_url(f"{self.root}/{relative}")

    def _dates_file(self):
        if not self._listing_read:
            self._listing_read = True
            try:
                raw = fetch_url(f"{self.root}/{DATES_FILE}")
                self._listing = None if raw is None else parse_dates_file(raw.decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001 - only the roster is lost
                log.info("Vehicle obs: %s has no readable %s: %s", self.name, DATES_FILE, exc)
        return self._listing

    def vehicles(self, session_day: date) -> set[str] | None:
        listing = self._dates_file()
        if listing is None or not listing[1]:
            return None
        wanted = set(file_dates(session_day))
        return {settings.SETTINGS.archive.broker_vehicle(vehicle)
                for vehicle, days in listing[1].items() if days & wanted}


def build_sources(entries: tuple[str, ...] | None = None,
                  archive: settings.ArchiveSettings | None = None,
                  fields: FieldMap | None = None) -> list[VehicleObsSource]:
    """The sources for ``[archive] vehicle_obs`` (default: the settings in use)."""
    archive = archive or settings.SETTINGS.archive
    fields = fields or settings.SETTINGS.obs_columns
    if entries is None:
        entries = archive.vehicle_obs
    if entries is None:
        entries = tuple(sorted(settings.SETTINGS.providers))
    return history.sources_from(
        entries, "vehicle_obs",
        lambda url: UrlVehicleObsSource(url, archive.vehicle_obs_path, fields),
        lambda path: FolderVehicleObsSource(path, archive.vehicle_obs_path, fields))
