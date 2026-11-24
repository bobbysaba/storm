"""
The ``[mqtt]`` table: which broker to use and how to log in.  The STORM GUI
(settings/, from storm.toml) and the headless publisher (storm-headless.toml)
both parse it here, so the two read the same schema.  See
https://bobbysaba.com/docs/storm/ref/configuration/.

Standard library only: this package runs on a Pi without the rest of STORM.
"""

import os
import ssl
from dataclasses import dataclass, replace
from pathlib import Path

from . import topics
from .config_keys import Warn, check_keys

TLS_MODES = ("none", "server", "mutual")

# environment variables that hold the broker passwords, so they can live in
# .env instead of the TOML file
PASSWORD_ENV = "STORM_MQTT_PASSWORD"
VIEWER_PASSWORD_ENV = "STORM_MQTT_VIEWER_PASSWORD"

_KEYS = {"host", "port", "tls", "ca_cert", "cert_file", "key_file",
         "username", "password", "client_id_prefix", "read_only", "topic_root", "daily_reset_utc",
         "shared_items_url"}


class MQTTSettingsError(ValueError):
    """Raised when the [mqtt] table is invalid."""


@dataclass(frozen=True)
class MQTTSettings:
    host: str = ""
    port: int = 8883
    # none: plain TCP; server: TLS, broker verified; mutual: TLS plus a client certificate
    tls: str = "server"
    # CA bundle for the broker; None means the system trust store
    ca_cert: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None
    username: str = ""
    password: str = ""
    # put in front of every client ID (some brokers only allow certain prefixes)
    client_id_prefix: str = ""
    # never publish: for a login the broker only lets subscribe
    read_only: bool = False
    # every topic sits under this (topics.py)
    topic_root: str = topics.DEFAULT_ROOT
    # hour (UTC) when shared annotations, drawings and scan sectors expire
    daily_reset_utc: int = 8
    # the day's shared items as JSON, read once at startup, for brokers that
    # don't send retained messages on wildcard subscriptions (the app only)
    shared_items_url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host)

    def client_id(self, base: str) -> str:
        return self.client_id_prefix + base

    def tls_files(self) -> list[tuple[str, Path]]:
        """(label, path) for each file the TLS mode loads."""
        files = []
        if self.tls != "none" and self.ca_cert is not None:
            files.append(("ca_cert", self.ca_cert))
        if self.tls == "mutual":
            files += [("cert_file", self.cert_file), ("key_file", self.key_file)]
        return files

    def missing_files(self) -> list[Path]:
        """The TLS files that aren't on disk."""
        return [p for _, p in self.tls_files() if not os.path.isfile(p)]

    def tls_context(self) -> ssl.SSLContext | None:
        """The TLS context for this login, or None for tls = "none".  Raises
        FileNotFoundError / ValueError / ssl.SSLError when a file is missing,
        empty, or the certificate and key don't match."""
        if self.tls == "none":
            return None
        for label, path in self.tls_files():
            if not os.path.isfile(path):
                raise FileNotFoundError(f"MQTT TLS {label} missing: {str(path)!r}")
            if os.path.getsize(path) == 0:
                raise ValueError(f"MQTT TLS {label} is empty: {str(path)!r}")
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if self.ca_cert is not None:
            ctx.load_verify_locations(cafile=str(self.ca_cert))
        if self.tls == "mutual":
            ctx.load_cert_chain(certfile=str(self.cert_file), keyfile=str(self.key_file))
        return ctx


def parse_mqtt(table, base_dir: Path, *, inherit: MQTTSettings | None = None,
               password_env: str = PASSWORD_ENV, name: str = "mqtt",
               warn: Warn | None = None) -> MQTTSettings:
    """
    Read an [mqtt]-style table.  Relative paths resolve against *base_dir*.
    Keys left out come from *inherit* (how [mqtt.viewer] borrows from
    [mqtt]), else the defaults.  The password comes from the *password_env*
    environment variable when it's set, else the table's ``password``.
    Sub-tables (like [mqtt.viewer]) are ignored here.  An unknown key is an
    error, or with *warn* a warning and left out (config_keys.check_keys).
    """
    if not isinstance(table, dict):
        raise MQTTSettingsError(f"[{name}] must be a table")
    table = check_keys(table, _KEYS, f"[{name}]", MQTTSettingsError, warn,
                       skip=lambda _k, v: isinstance(v, dict))

    base = inherit or MQTTSettings()

    tls = str(table.get("tls", base.tls)).strip().lower()
    if tls not in TLS_MODES:
        raise MQTTSettingsError(f"[{name}] tls must be one of {', '.join(TLS_MODES)}, not {tls!r}")

    default_port = base.port if inherit else (1883 if tls == "none" else 8883)
    try:
        port = int(table.get("port", default_port))
    except (TypeError, ValueError):
        raise MQTTSettingsError(f"[{name}] port must be a number") from None
    if not 0 < port < 65536:
        raise MQTTSettingsError(f"[{name}] port must be between 1 and 65535")

    read_only = table.get("read_only", base.read_only)
    if not isinstance(read_only, bool):
        raise MQTTSettingsError(f"[{name}] read_only must be true or false")

    def path(key):
        if key not in table:
            return getattr(base, key)
        value = str(table[key]).strip()
        return _resolve(value, base_dir) if value else None

    try:
        topic_root = topics.validate_root(table.get("topic_root", base.topic_root))
    except ValueError as e:
        raise MQTTSettingsError(f"[{name}] {e}") from None

    reset = table.get("daily_reset_utc", base.daily_reset_utc)
    if isinstance(reset, bool) or not isinstance(reset, int) or not 0 <= reset <= 23:
        raise MQTTSettingsError(f"[{name}] daily_reset_utc must be a whole hour from 0 to 23")

    shared_items_url = str(table.get("shared_items_url", base.shared_items_url)).strip()
    if shared_items_url and not shared_items_url.startswith(("http://", "https://")):
        raise MQTTSettingsError(f"[{name}] shared_items_url must be an http(s) URL")

    password = os.environ.get(password_env) or str(table.get("password", base.password if inherit else ""))

    s = replace(
        base,
        host=str(table.get("host", base.host)).strip(),
        port=port,
        tls=tls,
        ca_cert=path("ca_cert"),
        cert_file=path("cert_file"),
        key_file=path("key_file"),
        username=str(table.get("username", base.username)).strip(),
        password=password,
        client_id_prefix=str(table.get("client_id_prefix", base.client_id_prefix)).strip(),
        read_only=read_only,
        topic_root=topic_root,
        daily_reset_utc=reset,
        shared_items_url=shared_items_url,
    )
    if s.tls == "mutual" and not (s.cert_file and s.key_file):
        raise MQTTSettingsError(f'[{name}] tls = "mutual" needs cert_file and key_file')
    return s


def _resolve(value: str, base_dir: Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else base_dir / p
