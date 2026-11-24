"""File indexes NSSL publishes on THREDDS, so ARCHIVE finds a product's files
for a day without listing folders (and every product comes from THREDDS,
none from the NSSL API).

Each index is a JSON file in one of two shapes. A table:

    {
      "generated": "2026-11-21T16:00:00Z",
      "files": {"<platform>": {"YYYYMMDD": ["path/relative/to/the/index/folder", ...]}}
    }

or a list of entries, alone or as "files" (any other keys are ignored):

    [{"path": "probe1/raw/20240427.txt"}, {"path": "_legacy_data/probe1/20240427.txt"}, ...]

where an entry's platform is its "platform" or else the path's first folder
not starting with "_", and its day its "date" or else the 8-digit run in the
file name.

<platform> is the instrument the files belong to: "dltruck" for the lidar
truck's radiosondes, "CLAMPS1" / "CLAMPS2" for TROPoe, the vehicle ("probe1")
for the FOFS mobile mesonet. Paths are relative to the folder the index sits
in.

FOFS: the FOFS/Mobile-Mesonet tree is reorganized often (files are added
daily, folders move), so STORM doesn't assume a layout: NSSL's processing
machine writes data/manifest.json every hour, a list of every .txt file
under data/ (its "category" and "stage" keys aren't read), so that index is
re-read after an hour rather than a few. Not every indexed file is
readable: _legacy_data holds the pre-conversion originals of the published
files, in each logger era's own format, with exactly the same rows (and
_support files with none); the loader skips those (see
vehicle_obs_archive_fetcher). Without the index, the loader tries each
vehicle's usual location.

An index whose path is "" isn't published yet: get() answers None without
asking, and the fetchers list the product's folders as before. An index is
cached on disk for a few hours, and a failed fetch never replaces a good
cached copy.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from core import package_sources
from providers.nssl import paths

log = logging.getLogger(__name__)

# THREDDS paths of the indexes (below the fileServer root)
SONDE_INDEX_PATH = ""      # TODO(user): the lidar truck's radiosonde index
TROPOE_INDEX_PATH = ""     # TODO(user): the CLAMPS TROPoe index
FOFS_INDEX_PATH = f"{paths.FOFS_MESONET_DATA}/manifest.json"
FOFS_VEHICLE = re.compile(r"^[a-z0-9_]+$")

CACHE_DIR = Path.home() / ".cache" / "storm"
CACHE_TTL_S = 6 * 3600
FOFS_CACHE_TTL_S = 3600     # NSSL rebuilds the FOFS manifest hourly
# after a failed fetch with nothing cached, wait this long before asking
# again, so an unpublished index costs one request, not one per platform-day
RETRY_AFTER_S = 10 * 60


@dataclass(frozen=True)
class IndexedFile:
    platform: str
    date: date      # the date the index files it under -- not necessarily the data's
    path: str       # THREDDS path below the catalog / fileServer roots

    @property
    def url(self) -> str:
        return paths.file_url(self.path)


def _from_entries(entries: list) -> tuple[dict, int]:
    """A manifest's list of {"path": ...} entries as a "files" table. An
    entry may name its "platform" and "date" (YYYYMMDD); otherwise the
    platform is the path's first folder not starting with "_" and the date
    the 8-digit run in the file name."""
    by_platform: dict = {}
    skipped = 0
    for entry in entries:
        path = str(entry.get("path") or "").strip().lstrip("/") if isinstance(entry, dict) else ""
        folders = path.split("/")[:-1]
        platform = entry.get("platform") if isinstance(entry, dict) else None
        if platform is None:
            platform = next((f for f in folders if not f.startswith("_")), "")
        day = entry.get("date") if isinstance(entry, dict) else None
        if day is None:
            m = _FILE_DAY.search(path.rsplit("/", 1)[-1])
            day = m.group(1) if m else ""
        if not path or not platform or not day:
            skipped += 1
            continue
        by_platform.setdefault(str(platform), {}).setdefault(str(day), []).append(path)
    return by_platform, skipped


_FILE_DAY = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def parse_index(data: dict | list, root: str, name: str = "index",
                platform_pattern: re.Pattern | None = None) -> list[IndexedFile]:
    """The files an index lists, with paths made absolute under *root*.
    With *platform_pattern*, platforms are lowercased and must match it.
    Raises ValueError when it isn't an index; skips (and logs) entries it
    can't read."""
    by_platform = data.get("files") if isinstance(data, dict) else data
    skipped = 0
    if isinstance(by_platform, list) and by_platform:
        by_platform, skipped = _from_entries(by_platform)
    if not isinstance(by_platform, dict):
        raise ValueError(f'not a {name}: no "files" table or list')
    files = []
    for platform, by_day in by_platform.items():
        platform = str(platform).strip()
        if platform_pattern is not None:
            platform = platform.lower()
            if not platform_pattern.match(platform):
                platform = ""
        if not platform or not isinstance(by_day, dict):
            skipped += 1
            continue
        for day_text, rel_paths in by_day.items():
            try:
                day = datetime.strptime(str(day_text), "%Y%m%d").date()
            except ValueError:
                skipped += 1
                continue
            for rel in rel_paths if isinstance(rel_paths, list) else []:
                rel = str(rel).strip().lstrip("/")
                if not rel or ".." in rel.split("/"):
                    skipped += 1
                    continue
                files.append(IndexedFile(platform, day, f"{root}/{rel}" if root else rel))
    if skipped:
        log.warning("%s: skipped %d entries it couldn't read", name, skipped)
    files.sort(key=lambda f: (f.platform, f.date, f.path))
    return files


class Index:
    """An index's files, grouped by platform and day."""

    def __init__(self, files: list[IndexedFile], built_at: float):
        self.files = files
        self.built_at = built_at
        self._by_platform_date: dict[tuple[str, date], list[IndexedFile]] = {}
        for f in files:
            self._by_platform_date.setdefault((f.platform, f.date), []).append(f)

    def files_for(self, platform: str, day: date) -> list[IndexedFile]:
        return self._by_platform_date.get((platform, day), [])

    def platforms(self) -> set[str]:
        return {f.platform for f in self.files}

    def dates(self, platform: str | None = None) -> set[date]:
        return {f.date for f in self.files if platform is None or f.platform == platform}


class PublishedIndex:
    """One index on THREDDS, fetched when needed and cached."""

    def __init__(self, name: str, index_path: str, kind: str = "listings", *,
                 platform_pattern: re.Pattern | None = None, require_files: bool = False,
                 fallback: str = "listing its folders", max_age_s: float = CACHE_TTL_S):
        self.name = name
        self.max_age_s = max_age_s          # how long a fetched copy is used
        self.index_path = index_path.strip().strip("/")
        self.kind = kind                    # its provenance kind (case packages)
        self.platform_pattern = platform_pattern
        self.require_files = require_files  # an empty index is broken, not "no data"
        self.fallback = fallback            # what the fetchers do without it, for the log
        self._lock = threading.Lock()
        self._current: Index | None = None
        self._failed_at: float | None = None

    @property
    def published(self) -> bool:
        return bool(self.index_path)

    @property
    def root(self) -> str:
        return str(PurePosixPath(self.index_path).parent) if self.published else ""

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / f"nssl_{self.name}_index.json"

    def get(self, fetch_text: Callable[[], str | None] | None = None, *,
            max_age_s: float | None = None, now: Callable[[], float] = time.time) -> Index | None:
        """The index: from memory or disk when fresh, else fetched again; a
        stale copy when the fetch fails; None when it isn't published or
        there's nothing at all to go on."""
        if not self.published:
            return None
        if max_age_s is None:
            max_age_s = self.max_age_s
        with self._lock:
            if self._current is not None and now() - self._current.built_at <= max_age_s:
                return self._current
            cached = self._read_cache()
            if cached is not None and now() - cached.built_at <= max_age_s:
                self._current = cached
                return cached
            if self._failed_at is not None and now() - self._failed_at < RETRY_AFTER_S:
                return cached
            try:
                text = (fetch_text or self._fetch_text)()
                if text is None:
                    raise RuntimeError(f"{self.index_path} isn't published")
                files = parse_index(json.loads(text), self.root, f"{self.name} index", self.platform_pattern)
                if self.require_files and not files:
                    raise RuntimeError("the index lists no files")
                index = Index(files, now())
                log.info("NSSL %s index: %d files for %d platforms", self.name, len(files), len(index.platforms()))
            except Exception as exc:  # noqa: BLE001 - keep whatever we had
                log.warning("NSSL %s index unavailable (%s); %s", self.name, exc,
                            "using the older cached copy" if cached else self.fallback)
                self._current = cached
                self._failed_at = now()
                return cached
            self._failed_at = None
            self._write_cache(index)
            self._current = index
            return index

    def _fetch_text(self) -> str | None:
        from urllib.error import HTTPError
        from urllib.request import Request
        from data.auth import urlopen_with_retry
        from providers.nssl.archive.vehicle_obs_archive_fetcher import _USER_AGENT
        request = Request(paths.file_url(self.index_path), headers={"User-Agent": _USER_AGENT})
        try:
            return package_sources.read_url(self.kind, request, urlopen_with_retry, timeout=60).decode("utf-8")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def _read_cache(self) -> Index | None:
        try:
            raw = json.loads(self.cache_path.read_text())
            if raw.get("index_path") != self.index_path:
                return None
            files = [IndexedFile(f["platform"], date.fromisoformat(f["date"]), f["path"]) for f in raw["files"]]
            return Index(files, float(raw["built_at"]))
        except Exception:  # noqa: BLE001 - absent or unreadable cache
            return None

    def _write_cache(self, index: Index) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "index_path": self.index_path, "built_at": index.built_at,
                "files": [{"platform": f.platform, "date": f.date.isoformat(), "path": f.path}
                          for f in index.files]}))
            temporary.replace(self.cache_path)
        except Exception as exc:  # noqa: BLE001 - a cache is an optimization only
            log.warning("NSSL %s index cache not written: %s", self.name, exc)


SONDES = PublishedIndex("sondes", SONDE_INDEX_PATH, kind="instrument profiles")
TROPOE = PublishedIndex("tropoe", TROPOE_INDEX_PATH, kind="instrument profiles")
FOFS = PublishedIndex("fofs", FOFS_INDEX_PATH, kind="mesonet", platform_pattern=FOFS_VEHICLE,
                      require_files=True, fallback="using each vehicle's usual location",
                      max_age_s=FOFS_CACHE_TTL_S)
