"""
Recorded broker traffic for ARCHIVE mode: one JSON Lines file per topic kind
per UTC day, ``<prefix>.<kind>.<YYYYMMDD>`` (``.jsonl`` on the end is also
read), each line one payload as published (https://bobbysaba.com/docs/storm/ref/wire-format/). The layout
is in https://bobbysaba.com/docs/storm/ref/archive-data/.

Sources come from ``[archive] history`` in storm.toml, tried in order for
each file; the first one that has it wins:

    "recordings"             a folder (relative to the STORM folder)
    "https://host/storm"     a URL root, files fetched as <root>/<name> (with
                             the key and CA of the [[auth]] table covering it)
    "farm"                   a code provider's history_source() (https://bobbysaba.com/docs/storm/extend/providers/)

Without the setting: the provider sources of the providers turned on, then
the recorder's folder (``[archive] recordings``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import settings
from core import package_sources
from data import auth

log = logging.getLogger(__name__)

# what a file may end with after <prefix>.<kind>.<YYYYMMDD>
_SUFFIXES = ("", ".jsonl")


class HistorySource(Protocol):
    name: str

    def fetch(self, kind: str, date_str: str) -> Optional[str]:
        """The file's text, or None when the source has no such file. Raises
        when the source couldn't be read (then it's unknown, not empty)."""


def file_names(prefix: str, kind: str, date_str: str) -> list[str]:
    return [f"{prefix}.{kind}.{date_str}{suffix}" for suffix in _SUFFIXES]


class FolderSource:
    def __init__(self, folder: Path, prefix: str):
        self.folder = folder
        self.prefix = prefix
        self.name = f"folder {folder}"

    def fetch(self, kind: str, date_str: str) -> Optional[str]:
        # read through package_sources (by file:// URL) so case packages
        # carry the file and an open one serves it, on any machine
        for name in file_names(self.prefix, kind, date_str):
            path = self.folder / name
            data = package_sources.get_local(path, name)
            if data is None and path.is_file():
                data = package_sources.read_url("mqtt history", path.resolve().as_uri(),
                                                lambda _url, _path=path: _path.open("rb"))
            if data is not None:
                return data.decode("utf-8", errors="replace")
        return None


class UrlSource:
    def __init__(self, root: str, prefix: str):
        self.root = root.rstrip("/")
        self.prefix = prefix
        self.name = f"URL {self.root}"

    def fetch(self, kind: str, date_str: str) -> Optional[str]:
        for name in file_names(self.prefix, kind, date_str):
            text = fetch_url(f"{self.root}/{name}")
            if text is not None:
                return text
        return None


USER_AGENT = "Mozilla/5.0 STORM/1.0"


def fetch_bytes(url: str, kind: str, timeout: float) -> Optional[bytes]:
    """A file over HTTP(S), asked with its [[auth]] table's key and CA and
    recorded as *kind* for case packages; None on 404, raises on other
    errors. Also archive/vehicle_obs.py's."""
    full, headers, context = auth.request(url, {"User-Agent": USER_AGENT})
    try:
        return package_sources.read_url(kind, Request(full, headers=headers), urlopen,
                                        timeout=timeout, context=context)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def fetch_url(url: str) -> Optional[str]:
    """A history file's text over HTTP(S); None on 404."""
    data = fetch_bytes(url, "mqtt history", timeout=20)
    return None if data is None else data.decode("utf-8", errors="replace")


def sources_from(entries: tuple[str, ...], what: str, url_source: Callable[[str], object],
                 folder_source: Callable[[Path], object]) -> list:
    """The sources an ``[archive]`` list names (*what*: "history" or
    "vehicle_obs"), in order: URL roots, skipped while their [[auth]] key
    isn't set; code providers by name, through their ``<what>_source`` hook,
    skipped when off; anything else a folder, relative to the STORM folder."""
    from settings import code_providers  # noqa: PLC0415
    sources = []
    for entry in entries:
        if entry.startswith(("http://", "https://")):
            missing = auth.missing_key(entry)
            if missing:
                log.info("archive %s: %s needs %s, which isn't set; skipped", what, entry, missing)
            else:
                sources.append(url_source(entry))
        elif settings.is_provider_entry(entry):
            source = code_providers.archive_hook(settings.SETTINGS, entry, f"{what}_source")
            if source is None:
                log.info("archive %s: provider %r is off or has no %s source; skipped", what, entry, what)
            else:
                sources.append(source)
        else:
            path = Path(entry).expanduser()
            sources.append(folder_source(path if path.is_absolute() else settings.PROJECT_DIR / path))
    return sources


def build_sources(entries: tuple[str, ...] | None = None,
                  archive: settings.ArchiveSettings | None = None,
                  topic_root: str | None = None) -> list[HistorySource]:
    """The sources for ``[archive] history`` (default: the settings in use)."""
    archive = archive or settings.SETTINGS.archive
    if entries is None:
        entries = archive.history
    prefix = archive.history_prefix or settings.file_prefix(topic_root or settings.SETTINGS.mqtt.topic_root)
    if entries is None:
        entries = tuple(name for name in sorted(settings.SETTINGS.providers)) + (str(archive.recordings),)
    return sources_from(entries, "history", lambda url: UrlSource(url, prefix),
                        lambda path: FolderSource(path, prefix))


def fetch_text(sources: list[HistorySource], kind: str, date_str: str) -> tuple[Optional[str], str]:
    """The first source's file for this kind and day, as (text, source name);
    (None, "") when every source answered that it has none. Raises when none
    had it and at least one couldn't be read, since the file may be there."""
    errors = []
    for source in sources:
        try:
            text = source.fetch(kind, date_str)
        except Exception as exc:  # noqa: BLE001 - try the next source
            log.debug("archive history: %s failed for %s %s: %s", source.name, kind, date_str, exc)
            errors.append(f"{source.name}: {exc}")
            continue
        if text is not None:
            return text, source.name
    if errors:
        raise RuntimeError(f"no {kind} file for {date_str}; {'; '.join(errors)}")
    return None, ""
