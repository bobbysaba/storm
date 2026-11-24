"""Which archive dates have data, for the launch window's calendar and case finder.

Listings show availability, not data quality. Each platform's catalogs are
scanned once in the background, newest first; their dates are kept in
memory and on disk (24 hours, an hour for a catalog with dates this year)
and reused when the user changes dates. A failed check stays unknown: an
unshaded calendar day never proves there's no data.

Platforms come from what ARCHIVE mode reads (all_platforms):
- recorded broker traffic ([archive] history): a folder's file names, or a
  URL root's dates.json;
- vehicle obs ([archive] vehicle_obs): each vehicle in a folder, a URL
  root's dates.json, or a provider's own (NSSL's FOFS mesonet,
  providers/nssl/archive/catalog.py).
Local folders are listed on every scan, not cached. Campaigns for the case
finder are [[archive.campaigns]] in storm.toml.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Event

import settings

_DATE_RE = re.compile(r"(\d{8})")

_CACHE_TTL_SECONDS = 24 * 60 * 60
# a catalog with dates this year may still be gaining files
_ACTIVE_CACHE_TTL_SECONDS = 60 * 60
_DATE_CACHE_PATH = Path.home() / ".cache" / "storm" / "archive-dates.json"
log = logging.getLogger(__name__)

# case-finder headings (KnownPlatform.group), in the order listed
RECORDED = "RECORDED TRAFFIC"
VEHICLES = "VEHICLES"
INSTRUMENTS = "INSTRUMENTS"


def campaign_years() -> dict[str, tuple[int, ...]]:
    """[[archive.campaigns]]: campaign name -> its years."""
    return dict(settings.SETTINGS.archive.campaigns)


@dataclass(frozen=True)
class KnownPlatform:
    platform_id: str
    display_name: str
    family: str          # which kind of data, e.g. the mobile mesonet
    key: object
    site: str = ""       # the vehicle or instrument it belongs to, which the finder lists
    group: str = INSTRUMENTS   # the finder's heading for its site
    catalogs: tuple = ()       # where its dates come from (objects with url, year, scan)
    latest_year: int | None = None   # last year it had data, if known: scans newest first


class ScanCanceled(Exception):
    pass


def _check_cancel(cancel: Event):
    if cancel.is_set():
        raise ScanCanceled()


def _dates_from_filenames(filenames) -> list[date]:
    found = set()
    for name in filenames:
        match = _DATE_RE.search(name)
        if match:
            try:
                found.add(datetime.strptime(match[1], "%Y%m%d").date())
            except ValueError:
                pass
    return sorted(found)


def catalogs_for_platform(platform: KnownPlatform) -> tuple:
    if not platform.catalogs:
        raise ValueError(f"Platform without catalogs: {platform.platform_id}")
    return platform.catalogs


@dataclass(frozen=True)
class PlatformAvailability:
    dates: frozenset[date]
    checked: int
    total: int
    errors: tuple[str, ...]

    @property
    def complete(self):
        return self.checked == self.total and not self.errors


@dataclass(frozen=True)
class AvailabilitySnapshot:
    platforms: dict[str, PlatformAvailability]
    checked: int
    total: int
    cached: bool = False

    @property
    def dates(self):
        return frozenset(d for result in self.platforms.values() for d in result.dates)


class AvailabilityIndex:
    """Owned by one worker thread. Changing dates reuses this run's results;
    clear() (refresh) rechecks every catalog."""

    def __init__(self, platforms=None, fetch=None, pace=0.2, cache_path=None, now=None):
        self.platforms = tuple(all_platforms() if platforms is None else platforms)
        self._specs = {p.platform_id: catalogs_for_platform(p) for p in self.platforms}
        self._results = {}
        self._cached_dates = {}
        self._cache_records = {}
        self._cache_backed = set()
        self._clock = now or time.time
        # injected (test) sources don't touch the real cache unless given one
        default_cache = platforms is None and fetch is None
        self._cache_path = Path(cache_path) if cache_path is not None else (_DATE_CACHE_PATH if default_cache else None)
        self._latest_seen = {}
        # injected (test) fetch for listing pages; None: each catalog's own
        self._fetch = fetch
        self._pace = pace
        self._read_cache()

    def _read_cache(self):
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            if self._cache_path.stat().st_size > 16 * 1024 * 1024:
                return
            payload = json.loads(self._cache_path.read_text())
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return
            records = payload.get("catalogs", {})
            if not isinstance(records, dict):
                return
            for spec in {s for specs in self._specs.values() for s in specs if _cacheable(s)}:
                record = records.get(spec.url)
                if not record:
                    continue
                dates = frozenset(date.fromisoformat(d) for d in record["dates"])
                self._cached_dates[spec] = dates
                self._cache_records[spec.url] = record
                self._cache_backed.add(spec)
                if dates:
                    self._latest_seen[spec] = max(dates).year
                age = self._clock() - float(record["checked_at"])
                active = bool(dates) and max(dates).year >= datetime.fromtimestamp(self._clock()).year
                ttl = _ACTIVE_CACHE_TTL_SECONDS if active else _CACHE_TTL_SECONDS
                # a failed check shows its old dates but is retried
                if 0 <= age < ttl and not record.get("error"):
                    self._results[spec] = (dates, "")
        except (OSError, ValueError, TypeError, KeyError):
            log.warning("Ignoring unreadable archive date cache")

    def _write_cache(self, spec):
        if self._cache_path is None:
            return
        dates, error = self._results[spec]
        # a failure keeps the dates known before it, without calling them current
        if error:
            dates = dates | self._cached_dates.get(spec, frozenset())
            self._results[spec] = (dates, error)
        self._cache_records[spec.url] = dict(dates=sorted(d.isoformat() for d in dates),
                                            error=error, checked_at=self._clock())
        self._cached_dates[spec] = dates
        if not error:
            self._cache_backed.discard(spec)
        temporary = None
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self._cache_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "catalogs": self._cache_records}, stream)
            temporary.replace(self._cache_path)
        except OSError as exc:
            log.warning("Could not save archive date cache: %s", exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self):
        self._results.clear()
        self._cache_backed.update(self._cached_dates)

    def snapshot(self):
        platforms = {}
        for pid, specs in self._specs.items():
            results = [self._results[s] for s in specs if s in self._results]
            platforms[pid] = PlatformAvailability(
                frozenset(d for spec in specs
                          for d in self._results.get(spec, (self._cached_dates.get(spec, ()), ""))[0]),
                len(results), len(specs), tuple(error for _, error in results if error),
            )
        total = len({s for specs in self._specs.values() for s in specs})
        return AvailabilitySnapshot(platforms, len(self._results), total, bool(self._cache_backed))

    def _scan_order(self):
        """Recent sources first, then older ones; a platform's catalogs are
        interleaved with the others' so one platform can't hold up the rest."""
        priorities = {}
        for platform in self.platforms:
            hint = platform.latest_year or date.max.year
            for position, spec in enumerate(self._specs[platform.platform_id]):
                year = spec.year or self._latest_seen.get(spec, hint)
                priority = (-year, position)
                priorities[spec] = min(priorities.get(spec, priority), priority)
        return sorted(priorities, key=priorities.get)

    def scan(self, cancel: Event):
        _check_cancel(cancel)
        yield self.snapshot()
        for spec in self._scan_order():
            _check_cancel(cancel)
            if spec in self._results:
                continue
            if cancel.wait(self._pace):
                raise ScanCanceled()
            try:
                dates = frozenset(spec.scan(self._fetch, cancel))
                self._results[spec] = (dates, "")
                if dates:
                    self._latest_seen[spec] = max(dates).year
            except ScanCanceled:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed check stays unknown
                self._results[spec] = (frozenset(), f"{spec.url}: {exc}")
            _check_cancel(cancel)
            if _cacheable(spec):
                self._write_cache(spec)
            yield self.snapshot()




def _cacheable(spec) -> bool:
    """Local folders are listed every time; anything over the network is cached."""
    return getattr(spec, "cacheable", True)


@dataclass(frozen=True)
class HistoryFolderSpec:
    """Recorded broker traffic in a folder: the days in its file names."""
    folder: Path
    prefix: str
    year: int | None = None
    cacheable = False

    @property
    def url(self):
        return self.folder.as_uri() + "#history"

    def scan(self, fetch, cancel: Event) -> frozenset[date]:
        if not self.folder.is_dir():
            return frozenset()
        # <prefix>.<kind>.<YYYYMMDD>[.jsonl]: the date is after the prefix
        names = (p.name[len(self.prefix) + 1:] for p in self.folder.glob(f"{self.prefix}.*"))
        return frozenset(_dates_from_filenames(names))


@dataclass(frozen=True)
class VehicleFolderSpec:
    """One vehicle's days in a vehicle-obs folder."""
    source: object          # archive.vehicle_obs.FolderVehicleObsSource
    vehicle: str
    year: int | None = None
    cacheable = False

    @property
    def url(self):
        return self.source.folder.as_uri() + f"#{self.vehicle}"

    def scan(self, fetch, cancel: Event) -> frozenset[date]:
        return frozenset(self.source.dates().get(self.vehicle, ()))


@dataclass(frozen=True)
class DatesFileSpec:
    """A URL root's dates.json (archive/vehicle_obs.py parse_dates_file):
    every day it lists, top-level and per vehicle."""
    root: str
    year: int | None = None

    @property
    def url(self):
        return f"{self.root}/dates.json"

    def scan(self, fetch, cancel: Event) -> frozenset[date]:
        from archive import vehicle_obs
        if fetch is not None:
            text = fetch(self.url, cancel)
        else:
            raw = vehicle_obs.fetch_url(self.url)
            text = None if raw is None else raw.decode("utf-8", errors="replace")
        if not text:
            return frozenset()          # no dates.json: nothing shaded
        days, vehicles = vehicle_obs.parse_dates_file(text)
        return frozenset(days.union(*vehicles.values()))


def _history_platforms() -> list[KnownPlatform]:
    from archive import history
    platforms = []
    for source in history.build_sources():
        if isinstance(source, history.FolderSource):
            catalog = HistoryFolderSpec(source.folder, source.prefix)
        elif isinstance(source, history.UrlSource):
            catalog = DatesFileSpec(source.root)
        else:
            continue                    # a provider's history isn't listed
        platforms.append(KnownPlatform(f"history:{catalog.url}", source.name, "Recorded broker traffic",
                                       None, site="Recorded broker traffic", group=RECORDED,
                                       catalogs=(catalog,)))
    return platforms


def _vehicle_obs_platforms() -> list[KnownPlatform]:
    from archive import vehicle_obs
    platforms = []
    for source in vehicle_obs.build_sources():
        if isinstance(source, vehicle_obs.FolderVehicleObsSource):
            for vehicle in sorted(source.dates()):
                platforms.append(KnownPlatform(f"obs:{source.folder.as_uri()}#{vehicle}", vehicle,
                                               "Vehicle obs", vehicle, site=vehicle, group=VEHICLES,
                                               catalogs=(VehicleFolderSpec(source, vehicle),)))
        elif isinstance(source, vehicle_obs.UrlVehicleObsSource):
            catalog = DatesFileSpec(source.root)
            platforms.append(KnownPlatform(f"obs:{catalog.url}", source.name, "Vehicle obs", None,
                                           site=f"Vehicle obs ({source.root})", group=VEHICLES,
                                           catalogs=(catalog,)))
        else:
            # a provider's own (NSSL: the FOFS mesonet vehicles)
            platforms += getattr(source, "calendar_platforms", lambda: [])()
    return platforms


def all_platforms() -> list[KnownPlatform]:
    """Every platform the calendar scans, from the [archive] sources in use."""
    platforms, seen = [], set()
    for platform in _history_platforms() + _vehicle_obs_platforms():
        if platform.platform_id not in seen:
            seen.add(platform.platform_id)
            platforms.append(platform)
    return platforms


def platforms_by_site(platforms=None) -> dict[str, list[KnownPlatform]]:
    """Platforms grouped by the vehicle or instrument they belong to."""
    grouped = {}
    for platform in all_platforms() if platforms is None else platforms:
        grouped.setdefault(platform.site, []).append(platform)
    return grouped
