"""
Soundings from storm.toml: the ``[[sources]]`` of kind ``sounding``
(data/sources.py), and a code provider's sounding sources. Fetched when its
SOUNDING drawer button is picked or its comparison pill clicked, then every
refresh_min while the Skew-T viewer shows it (ui/app/main_window_sounding.py).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import SoundingSet
from data.sources import (
    Source,
    code_soundings,
    levels_to_sounding,
    profile_levels,
    profile_time,
    read_launches,
    sounding_set,
)

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
_TIMEOUT = 20


class SoundingSourceError(RuntimeError):
    pass


def http_get(source: Source, url: str) -> tuple[bytes, datetime | None]:
    """(body, Last-Modified) for a request to *source*'s server, with its key."""
    full, headers = source.request(url)
    try:
        with urlopen(Request(full, headers={**_HEADERS, **headers}), timeout=_TIMEOUT,
                     context=source.ssl_context(url)) as resp:
            body = resp.read()
            modified = resp.headers.get("Last-Modified")
    except HTTPError as exc:
        raise SoundingSourceError(f"HTTP {exc.code} from {url}") from None
    except URLError as exc:
        raise SoundingSourceError(f"network error from {url}: {exc.reason}") from None
    try:
        when = parsedate_to_datetime(modified).astimezone(timezone.utc) if modified else None
    except (TypeError, ValueError):
        when = None
    return body, when


def fetch_sounding_set(source: Source, get=http_get, now: datetime | None = None) -> SoundingSet:
    """A sounding source's launches within lookback_hours (or its one
    profile) as a SoundingSet. Raises when there's nothing to show."""
    now = now or datetime.now(timezone.utc)
    site = source.site or (None, None)
    if source.fetch is not None:
        soundings = code_soundings(source, source.fetch())
    elif not source.index:
        raw, modified = get(source, source.url)
        when = profile_time(source, raw, modified or now)
        snd = levels_to_sounding(profile_levels(source, raw), when, *site)
        soundings = [snd] if snd is not None else []
    else:
        raw, _ = get(source, source.url)
        try:
            index = json.loads(raw.decode("utf-8-sig", errors="replace"))
        except ValueError:
            raise SoundingSourceError(f"{source.short}: the index at {source.url} is not JSON") from None
        try:
            launches = read_launches(source, index, now)
        except ValueError as exc:
            raise SoundingSourceError(f"{source.short} index: {exc}") from None
        if not launches:
            raise SoundingSourceError(
                f"{source.short}: no launches in the last {source.lookback_hours:g} hours")
        soundings = []
        for launch in launches:
            try:
                raw, _ = get(source, launch.url)
                lat, lon = (launch.lat, launch.lon) if launch.lat is not None else site
                snd = levels_to_sounding(profile_levels(source, raw), launch.time, lat, lon)
            except Exception as exc:  # noqa: BLE001 - one bad profile mustn't hide the rest
                log.warning("sounding %s: %s: %s", source.id, launch.url, exc)
                continue
            if snd is None:
                log.warning("sounding %s: %s has too few usable levels", source.id, launch.url)
            else:
                soundings.append(snd)
    if not soundings:
        raise SoundingSourceError(f"{source.short}: no profile could be read")
    return sounding_set(source, soundings, now)


class SourceSoundingFetcher(QObject):
    """Fetches one sounding source at a time, in the background."""

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error = pyqtSignal(str)

    def __init__(self, sources: tuple[Source, ...], parent=None):
        super().__init__(parent)
        self.sources: dict[str, Source] = {s.id: s for s in sources}
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def fetch(self, source_id: str) -> bool:
        source = self.sources.get(source_id)
        if source is None:
            return False
        with self._lock:
            if source_id in self._busy:
                return False
            self._busy.add(source_id)
        threading.Thread(target=self._worker, args=(source,), daemon=True,
                         name=f"sounding-{source_id}").start()
        return True

    def _worker(self, source: Source) -> None:
        try:
            self.sounding_ready.emit(fetch_sounding_set(source))
        except Exception as exc:  # noqa: BLE001 - a bad source mustn't stop the app
            log.warning("sounding %s: %s", source.id, exc)
            message = str(exc)
            if source.api_key_param and source.api_key:
                message = message.replace(source.api_key, "***")
            self.fetch_error.emit(message)
        finally:
            with self._lock:
                self._busy.discard(source.id)
