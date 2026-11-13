"""Which archive dates have data, for the launch window's calendar and case finder.

Listings show availability, not data quality. Each registered source's
catalogs are scanned once in the background, newest sources first; their
dates are kept in memory and on disk (24 hours, an hour for a catalog with
dates this year) and reused when the user changes dates. A failed check
stays unknown: an unshaded calendar day never proves there's no data.

Sources registered here are the ones ARCHIVE mode reads: the FOFS mobile
mesonet vehicles (dates from the crawled file index, archive/fofs_index.py).
Flat THREDDS listings (CatalogSpec) are scanned too, for instruments added
by later sessions (CLAMPS, lidar).
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from config import NSSL_SSL_CONTEXT
from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS
from data import thredds_paths as paths

_REQUEST_TIMEOUT = 8
_MAX_CATALOG_BYTES = 16 * 1024 * 1024
_DATE_RE = re.compile(r"(\d{8})")
CAMPAIGN_YEARS = {
    "VORTEX2": (2009, 2010), "RiVorS": (2017,), "TORUS": (2019, 2022, 2023),
    "PERiLS": (2022, 2023), "LIFT": (2024, 2025, 2026),
}

# The last year each source had data (the fork's 2026-09-08 catalog audit).
# Only orders the scan, newest first; every source is still checked.
_LATEST_OBSERVED_YEAR = {
    "FOFS-dltruck": 2026, "FOFS-farfield": 2022, "FOFS-hailcam": 2026,
    "FOFS-mg1": 2015, "FOFS-mg2": 2015, "FOFS-mg3": 2015, "FOFS-noxp_scout": 2015,
    "FOFS-probe1": 2026, "FOFS-probe2": 2026, "FOFS-probe3": 2010, "FOFS-probe4": 2010,
    "FOFS-probe5": 2010, "FOFS-probe7": 2010, "FOFS-probe9": 2010,
    "FOFS-windsonde1": 2022, "FOFS-windsonde2": 2022,
}

_CACHE_TTL_SECONDS = 24 * 60 * 60
# a catalog with dates this year may still be gaining files
_ACTIVE_CACHE_TTL_SECONDS = 60 * 60
_DATE_CACHE_PATH = Path.home() / ".cache" / "storm" / "archive-dates.json"
_USER_AGENT = "Mozilla/5.0 (STORM archive date finder)"
log = logging.getLogger(__name__)

MESONET = "FOFS Mobile Mesonet"


@dataclass(frozen=True)
class KnownPlatform:
    platform_id: str
    display_name: str
    family: str          # which kind of data (one fetcher), e.g. the mobile mesonet
    key: object
    site: str = ""       # the vehicle or instrument it belongs to, which the finder lists


class ScanCanceled(Exception):
    pass


def _check_cancel(cancel: Event):
    if cancel.is_set():
        raise ScanCanceled()


def _fetch_catalog_html(url: str, cancel: Event) -> str:
    """One request with one retry, both interruptible. A 404 is an absent
    folder (""); a 410 stays an error, since this host has returned it for
    catalogs that open in a browser."""
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    for attempt in range(2):
        _check_cancel(cancel)
        try:
            with urlopen(request, timeout=_REQUEST_TIMEOUT, context=NSSL_SSL_CONTEXT) as response:
                chunks, size = [], 0
                deadline = time.monotonic() + 15
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _MAX_CATALOG_BYTES or time.monotonic() > deadline:
                        raise ValueError("Catalog exceeds listing size/time limit")
                html = b"".join(chunks).decode("utf-8", errors="replace")
                if "thredds" not in html.lower() or "catalog" not in html.lower():
                    raise ValueError("Server did not return a THREDDS catalog")
                return html
        except HTTPError as exc:
            if exc.code == 404:
                return ""
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise
        except (URLError, TimeoutError):
            if attempt:
                raise
        if cancel.wait(0.75):
            raise ScanCanceled()
    raise AssertionError("unreachable")


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


@dataclass(frozen=True)
class CatalogSpec:
    """One flat THREDDS folder listing; its files' names carry the dates."""
    path: str
    suffix: str | tuple[str, ...]
    year: int | None = None      # only for catalogs split by year

    @property
    def url(self):
        return paths.catalog_url(self.path)


@dataclass(frozen=True)
class FofsIndexSpec:
    """A mesonet vehicle's dates from the crawled FOFS file index, since
    that tree is reorganized often. vehicle=None collects every folder no
    registered vehicle covers, so a new vehicle's dates still show."""
    vehicle: str | None
    year: int | None = None

    @property
    def url(self):
        return paths.catalog_url(paths.FOFS_MESONET) + f"#{self.vehicle or 'other-vehicles'}"


class _CatalogLinks(HTMLParser):
    def __init__(self, spec, *, dates_only=False):
        super().__init__()
        self.spec = spec
        self.filenames = set()
        self.date_stamps = set()
        self._dates_only = dates_only
        self._host = urlsplit(spec.url).hostname

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlsplit(urljoin(self.spec.url, href))
        if parsed.hostname != self._host:
            return
        dataset = parse_qs(parsed.query).get("dataset", [None])[0]
        path = dataset if dataset is not None else unquote(parsed.path).removeprefix("/thredds/fileServer/")
        prefix = self.spec.path + "/"
        if path.startswith(prefix):
            name = path[len(prefix):]
            if "/" not in name and name.endswith(self.spec.suffix):
                if self._dates_only:
                    match = _DATE_RE.search(name)
                    if match:
                        self.date_stamps.add(match[1])
                else:
                    self.filenames.add(name)


def catalogs_for_platform(platform: KnownPlatform) -> tuple[CatalogSpec | FofsIndexSpec, ...]:
    if platform.family == MESONET:
        return (FofsIndexSpec(platform.key),)
    raise ValueError(f"Unregistered source family: {platform.family}")


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
        self.platforms = tuple(ALL_PLATFORMS if platforms is None else platforms)
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
        self._fetch = fetch or _fetch_catalog_html
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
            for spec in {s for specs in self._specs.values() for s in specs}:
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
            hint = _LATEST_OBSERVED_YEAR.get(platform.platform_id, date.max.year)
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
                if isinstance(spec, FofsIndexSpec):
                    from archive import fofs_index
                    index = fofs_index.get_index()
                    if index is None:
                        raise RuntimeError("FOFS file index unavailable")
                    vehicles = ({spec.vehicle} if spec.vehicle is not None
                                else index.vehicles() - set(KNOWN_FOFS_PLATFORMS))
                    dates = frozenset(d for v in vehicles for d in index.dates(v))
                else:
                    html = self._fetch(spec.url, cancel)
                    parser = _CatalogLinks(spec, dates_only=True)
                    parser.feed(html)
                    dates = frozenset(_dates_from_filenames(parser.date_stamps))
                self._results[spec] = (dates, "")
                if dates:
                    self._latest_seen[spec] = max(dates).year
            except ScanCanceled:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed check stays unknown
                self._results[spec] = (frozenset(), f"{spec.url}: {exc}")
            _check_cancel(cancel)
            self._write_cache(spec)
            yield self.snapshot()


# what people in the field call each vehicle; mg1-3 and noxp_scout aren't
# expanded, since what they stand for isn't confirmed
_FOFS_STAGE_NAMES: dict[str, str] = {
    "dltruck": "LiDAR Truck",
    "farfield": "FarField",
    "hailcam": "HailCam",
    "noxp_scout": "NOXP Scout",
    "probe1": "Probe 1", "probe2": "Probe 2", "probe3": "Probe 3",
    "probe4": "Probe 4", "probe5": "Probe 5", "probe7": "Probe 7",
    "probe9": "Probe 9",
    "windsonde1": "WindSonde 1", "windsonde2": "WindSonde 2",
    "mg1": "MG1", "mg2": "MG2", "mg3": "MG3",
}


def _build_registry() -> list[KnownPlatform]:
    platforms = [
        KnownPlatform(f"FOFS-{vehicle}", _FOFS_STAGE_NAMES.get(vehicle, vehicle), MESONET, vehicle,
                      site=_FOFS_STAGE_NAMES.get(vehicle, vehicle))
        for vehicle in KNOWN_FOFS_PLATFORMS
    ]
    # vehicle folders THREDDS adds that aren't registered still reach the calendar
    platforms.append(KnownPlatform("FOFS-other", "Other mesonet vehicles", MESONET, None,
                                   site="Other mesonet vehicles"))
    return platforms


ALL_PLATFORMS: list[KnownPlatform] = _build_registry()


def platforms_by_site() -> dict[str, list[KnownPlatform]]:
    """Platforms grouped by the vehicle or instrument they belong to."""
    grouped = {}
    for platform in ALL_PLATFORMS:
        grouped.setdefault(platform.site, []).append(platform)
    return grouped
