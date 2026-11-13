"""Every file STORM downloads in archive mode goes through here, so that

- an opened case package (core/case_package.py) serves the files it
  carries, checked against their recorded SHA-256, before any network
  request. Anything the package doesn't hold (left out at export, or not
  loaded when it was made) is downloaded from its source as usual;
- every file is recorded in the session's provenance (core/provenance.py),
  so the case can be exported and reproduced.

`requests_get` stands in for requests.get and `read_url` for
urlopen(...).read(); network errors (404 and so on) behave as before. Files
are matched by their full URL, query string included. With no package open
and provenance off (live modes), both are plain downloads.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import zipfile
from collections import Counter
from pathlib import Path

from core import provenance

log = logging.getLogger(__name__)

_lock = threading.Lock()
_package: Path | None = None
_zip: zipfile.ZipFile | None = None       # opened once: a large package's directory is slow to parse
_entries: dict[str, dict] = {}           # url -> {"member", "sha256", "kind"}
_served: Counter = Counter()


def activate(package_path: Path, data_entries: list[dict]) -> None:
    """Serve the data files listed in a package's manifest ("data" entries)."""
    global _package, _zip
    package_path = Path(package_path)
    try:
        opened = zipfile.ZipFile(package_path)
    except (OSError, zipfile.BadZipFile) as exc:
        log.warning("Case package %s could not be opened: %s", package_path.name, exc)
        return
    with _lock:
        if _zip is not None:
            _zip.close()
        _package, _zip = package_path, opened
        _entries.clear()
        _entries.update({e["url"]: e for e in data_entries if e.get("member") and e.get("url")})
        _served.clear()
    log.info("Case package %s: serving %d data files", package_path.name, len(_entries))


def deactivate() -> None:
    global _package, _zip
    with _lock:
        if _zip is not None:
            _zip.close()
        _package, _zip = None, None
        _entries.clear()
        _served.clear()


def active_package() -> Path | None:
    with _lock:
        return _package


def served() -> Counter:
    """Files served from the package so far, by kind."""
    with _lock:
        return Counter(_served)


def get(url: str) -> bytes | None:
    """The package's copy of `url`, or None if it doesn't carry it (or its
    copy doesn't match the recorded hash, so the network is used)."""
    with _lock:
        entry, z = _entries.get(url), _zip
        if entry is None or z is None:
            return None
        try:
            data = z.read(entry["member"])
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            log.warning("Case package: could not read %s (%s); downloading instead", entry["member"], exc)
            return None
    if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
        log.warning("Case package: %s does not match its recorded hash; downloading instead", entry["member"])
        return None
    with _lock:
        _served[entry.get("kind", "")] += 1
    provenance.record(entry.get("kind", ""), url, data, source="case package")
    return data


def canonical_url(url: str, params=None) -> str:
    """The URL requests would fetch for `url` with `params`."""
    if not params:
        return url
    import requests
    return requests.Request("GET", url, params=params).prepare().url


class _PackagedResponse:
    """Enough of requests.Response for the fetchers that use it."""

    status_code = 200
    ok = True

    def __init__(self, url: str, content: bytes):
        self.url, self.content = url, content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        return None


def requests_get(kind: str, url: str, params=None, **kwargs):
    """requests.get, package first; successful responses are recorded."""
    import requests
    full = canonical_url(url, params)
    data = get(full)
    if data is not None:
        return _PackagedResponse(full, data)
    kwargs.pop("stream", None)          # the whole body is read to hash it anyway
    if params is not None:
        kwargs["params"] = params
    response = requests.get(url, **kwargs)
    content = getattr(response, "content", None)
    if getattr(response, "status_code", 200) == 200 and isinstance(content, (bytes, bytearray)):
        provenance.record(kind, getattr(response, "url", None) or full, bytes(content))
    return response


def read_url(kind: str, request, opener, **kwargs) -> bytes:
    """`with opener(request, **kwargs) as r: return r.read()`, package first."""
    url = request if isinstance(request, str) else request.full_url
    data = get(url)
    if data is not None:
        return data
    with opener(request, **kwargs) as response:
        data = response.read()
    provenance.record(kind, url, data)
    return data
