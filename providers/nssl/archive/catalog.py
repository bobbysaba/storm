"""NSSL's part of the archive calendar (archive/catalog.py): the FOFS mobile
mesonet vehicles, dated from the file index NSSL publishes
(providers/nssl/indexes.py FOFS), and flat THREDDS folder listings
(CatalogSpec) for instruments that are listed by folder (none registered
yet). Only on the calendar while FOFS is one of the [archive] vehicle_obs
sources, which needs the NSSL provider on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from data import auth
import providers.nssl as nssl
from archive.catalog import KnownPlatform, ScanCanceled, _DATE_RE, _check_cancel, _dates_from_filenames
from providers.nssl import paths
from providers.nssl.archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS

_REQUEST_TIMEOUT = 8
_MAX_CATALOG_BYTES = 16 * 1024 * 1024
_USER_AGENT = "Mozilla/5.0 (STORM archive date finder)"

MESONET = "FOFS Mobile Mesonet"

# The last year each vehicle had data (the fork's 2026-09-08 catalog audit).
# Only orders the scan, newest first; every source is still checked.
_LATEST_OBSERVED_YEAR = {
    "FOFS-dltruck": 2026, "FOFS-farfield": 2022, "FOFS-hailcam": 2026,
    "FOFS-mg1": 2015, "FOFS-mg2": 2015, "FOFS-mg3": 2015, "FOFS-noxp_scout": 2015,
    "FOFS-probe1": 2026, "FOFS-probe2": 2026, "FOFS-probe3": 2010, "FOFS-probe4": 2010,
    "FOFS-probe5": 2010, "FOFS-probe7": 2010, "FOFS-probe9": 2010,
    "FOFS-windsonde1": 2022, "FOFS-windsonde2": 2022,
}

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


def _fetch_catalog_html(url: str, cancel: Event) -> str:
    """One request with one retry, both interruptible. A 404 is an absent
    folder (""); a 410 stays an error, since this host has returned it for
    catalogs that open in a browser."""
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    for attempt in range(2):
        _check_cancel(cancel)
        try:
            with urlopen(request, timeout=_REQUEST_TIMEOUT, context=auth.ssl_context(url)) as response:
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


@dataclass(frozen=True)
class CatalogSpec:
    """One flat THREDDS folder listing; its files' names carry the dates."""
    path: str
    suffix: str | tuple[str, ...]
    year: int | None = None      # only for catalogs split by year

    @property
    def url(self):
        return paths.catalog_url(self.path)

    def scan(self, fetch, cancel: Event) -> frozenset[date]:
        html = (fetch or _fetch_catalog_html)(self.url, cancel)
        parser = _CatalogLinks(self, dates_only=True)
        parser.feed(html)
        return frozenset(_dates_from_filenames(parser.date_stamps))


@dataclass(frozen=True)
class FofsIndexSpec:
    """A mesonet vehicle's dates from the published FOFS file index, since
    that tree is reorganized often. vehicle=None collects every vehicle no
    registered one covers, so a new vehicle's dates still show."""
    vehicle: str | None
    year: int | None = None

    @property
    def url(self):
        from providers.nssl import indexes
        return paths.file_url(indexes.FOFS.index_path) + f"#{self.vehicle or 'other-vehicles'}"

    def scan(self, fetch, cancel: Event) -> frozenset[date]:
        from providers.nssl import indexes
        index = indexes.FOFS.get()
        if index is None:
            raise RuntimeError("FOFS file index unavailable")
        vehicles = ({self.vehicle} if self.vehicle is not None
                    else index.platforms() - set(KNOWN_FOFS_PLATFORMS))
        return frozenset(d for v in vehicles for d in index.dates(v))


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


def _build_registry() -> list[KnownPlatform]:
    def platform(platform_id, name, vehicle):
        return KnownPlatform(platform_id, name, MESONET, vehicle, site=name, group="MOBILE MESONET",
                             catalogs=(FofsIndexSpec(vehicle),),
                             latest_year=_LATEST_OBSERVED_YEAR.get(platform_id))
    platforms = [platform(f"FOFS-{v}", _FOFS_STAGE_NAMES.get(v, v), v) for v in KNOWN_FOFS_PLATFORMS]
    # vehicle folders THREDDS adds that aren't registered still reach the calendar
    platforms.append(platform("FOFS-other", "Other mesonet vehicles", None))
    return platforms


FOFS_PLATFORMS: list[KnownPlatform] = _build_registry()
