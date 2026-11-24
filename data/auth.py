"""[[auth]] in storm.toml: how STORM asks one server for data, wherever the
URL comes from ([[sources]], [archive] history, a provider's fetchers, a case
package). Each table covers the URLs that start with its ``url``:

    [[auth]]
    url = "https://api.example.org"     # this server, or a folder on it
    api_key_env = "EXAMPLE_KEY"         # .env key sent as api_key_header (X-API-Key)
    ca_cert = "certs/extra-ca.pem"      # an extra CA to trust for it

The longest matching ``url`` wins, and a key is never sent to a URL no table
covers. Every key is in docs/configuration.md.
"""
from __future__ import annotations

import functools
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from headless.storm_headless.config_keys import Warn, check_keys

_KEYS = {"url", "api_key_env", "api_key_header", "api_key_param", "ca_cert"}
KEY_FIELDS = ("api_key_env", "api_key_header", "api_key_param", "ca_cert")


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class Auth:
    # the URLs it covers: this one and those below it
    url: str = ""
    # environment variable (or .env key) holding the API key, sent as
    # api_key_header, or as the query parameter api_key_param
    api_key_env: str = ""
    api_key_header: str = "X-API-Key"
    api_key_param: str = ""
    # extra CA certificate to trust for this server
    ca_cert: Path | None = None

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    def missing_key(self) -> str:
        """The environment variable this server needs but doesn't have, or ""."""
        return self.api_key_env if self.api_key_env and not self.api_key else ""

    def covers(self, url: str) -> bool:
        """*url* is this one or below it (not just a longer host name)."""
        prefix = self.url.rstrip("/")
        return url.startswith(prefix) and (len(url) == len(prefix) or url[len(prefix)] in "/?#")

    def request(self, url: str) -> tuple[str, dict[str, str]]:
        """(url, headers) with the key, if there is one."""
        key = self.api_key
        if not key:
            return url, {}
        if self.api_key_param:
            parts = urlsplit(url)
            query = parse_qsl(parts.query, keep_blank_values=True) + [(self.api_key_param, key)]
            return urlunsplit(parts._replace(query=urlencode(query))), {}
        return url, {self.api_key_header: key}

    def ssl_context(self) -> ssl.SSLContext | None:
        return _context(str(self.ca_cert)) if self.ca_cert is not None else None


@functools.lru_cache(maxsize=None)
def _context(ca_cert: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=ca_cert)
    except (OSError, ssl.SSLError):
        pass
    return ctx


def parse_key_fields(table: dict, where: str, project_dir: Path) -> dict:
    """api_key_env, api_key_header, api_key_param and ca_cert from a table
    ([[auth]] or [[sources]]), checked. Relative ca_cert paths resolve
    against *project_dir*."""
    def text(key, default=""):
        value = table.get(key, default)
        if not isinstance(value, str):
            raise AuthError(f"{where}: {key} must be a string")
        return value.strip()

    api_key_env = text("api_key_env")
    api_key_header = text("api_key_header", "X-API-Key")
    api_key_param = text("api_key_param")
    if (api_key_param or "api_key_header" in table) and not api_key_env:
        raise AuthError(f"{where}: api_key_header and api_key_param need api_key_env")
    if api_key_param and "api_key_header" in table:
        raise AuthError(f"{where}: give api_key_header or api_key_param, not both")
    ca_cert = None
    if "ca_cert" in table:
        raw = text("ca_cert")
        if not raw:
            raise AuthError(f"{where}: ca_cert must be a file path")
        path = Path(raw).expanduser()
        ca_cert = path if path.is_absolute() else project_dir / path
    return dict(api_key_env=api_key_env, api_key_header=api_key_header,
                api_key_param=api_key_param, ca_cert=ca_cert)


def parse_auth(raw, project_dir: Path, warn: Warn | None = None) -> tuple[Auth, ...]:
    """The [[auth]] array of tables, checked. With *warn*, an invalid table
    is a warning and left out, and so is an unknown key; without, either is
    an error."""
    if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
        raise AuthError("[[auth]] must be tables (one [[auth]] header each)")
    out, seen = [], set()
    for table in raw:
        try:
            found = _parse_one(table, project_dir, seen, warn)
        except AuthError as e:
            if warn is None:
                raise
            warn(f"{e} (table left out)")
            continue
        seen.add(found.url)
        out.append(found)
    return tuple(out)


def _parse_one(table: dict, project_dir: Path, seen: set, warn: Warn | None) -> Auth:
    url = table.get("url")
    if not isinstance(url, str) or not urlsplit(url.strip()).netloc \
            or not url.strip().startswith(("http://", "https://")):
        raise AuthError("[[auth]]: url must be an http(s) URL, such as \"https://api.example.org\"")
    url = url.strip().rstrip("/")
    where = f"[[auth]] {url}"
    table = check_keys(table, _KEYS, where, AuthError, warn)
    if url in seen:
        raise AuthError(f"{where}: this url is listed twice")
    fields = parse_key_fields(table, where, project_dir)
    if not fields["api_key_env"] and fields["ca_cert"] is None:
        raise AuthError(f"{where}: give api_key_env, ca_cert or both")
    return Auth(url=url, **fields)


def for_url(url: str, tables: tuple[Auth, ...] | None = None) -> Auth | None:
    """The [[auth]] table covering *url* (the longest match), or None.
    *tables* defaults to the settings in use: storm.toml's tables, then for
    a URL none of them covers, the code providers' own (their AUTH lists)."""
    if tables is None:
        import settings  # noqa: PLC0415 - settings imports this module
        found = _longest(settings.SETTINGS.auth, url)
        if found is None:
            from settings import code_providers  # noqa: PLC0415
            found = _longest(code_providers.auth_tables(settings.SETTINGS), url)
        return found
    return _longest(tables, url)


def _longest(tables, url: str) -> Auth | None:
    matches = [a for a in tables if a.covers(url)]
    return max(matches, key=lambda a: len(a.url)) if matches else None


def request(url: str, headers: dict[str, str] | None = None,
            tables: tuple[Auth, ...] | None = None) -> tuple[str, dict[str, str], ssl.SSLContext | None]:
    """(url, headers, SSL context) to ask *url* with: *headers* plus its
    [[auth]] table's key, and that table's CA."""
    headers = dict(headers or {})
    found = for_url(url, tables)
    if found is None:
        return url, headers, None
    url, extra = found.request(url)
    headers.update(extra)
    return url, headers, found.ssl_context()


def ssl_context(url: str) -> ssl.SSLContext | None:
    """The SSL context to ask *url* with (its [[auth]] table's CA), or None."""
    found = for_url(url)
    return found.ssl_context() if found is not None else None


RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_S = (1.5, 4.0)  # sleep before each retry attempt


def urlopen_with_retry(request: Request, *, timeout: float, backoff_s: tuple[float, ...] = RETRY_BACKOFF_S):
    """urlopen with *request*'s [[auth]] CA, retried after each of *backoff_s*
    on timeouts, connection errors and 429/5xx. Any other HTTP error (404,
    410) is a real answer (no data / blocked) and is never retried."""
    attempts = len(backoff_s) + 1
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=timeout, context=ssl_context(request.full_url))
        except HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_CODES or attempt == attempts - 1:
                raise
        except URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(backoff_s[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def missing_key(url: str) -> str:
    """The environment variable *url*'s [[auth]] table needs but isn't set, or ""."""
    found = for_url(url)
    return found.missing_key() if found is not None else ""
