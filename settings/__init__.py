"""
Per-install settings: ``storm.toml`` in the project folder (or the file named
by ``STORM_CONFIG``) and secrets from ``.env``.  config.py keeps the constants
that are the same for everyone.  Every setting is described in
docs/configuration.md; storm.example.toml and profiles/ are starting points.

The headless publisher parses the same [mqtt] and [obs_columns] tables with
the same code (headless/storm_headless/mqtt_settings.py, obs_columns.py).
[[sources]] is parsed by data/sources.py and [[auth]] by data/auth.py; a code
provider's package is loaded by settings/code_providers.py.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from headless.storm_headless.mqtt_settings import (
    VIEWER_PASSWORD_ENV,
    MQTTSettings,
    MQTTSettingsError,
    parse_mqtt,
)
from headless.storm_headless.config_keys import Warn, check_keys, did_you_mean
from data.auth import Auth, AuthError, parse_auth
from data.sources import Source, SourceError, parse_sources
from headless.storm_headless.obs_columns import FieldMap, ObsColumnsError, parse_obs_layouts, pick_layout

PROJECT_DIR = Path(__file__).resolve().parent.parent

# modes that publish, and so need a broker login that may
_PUBLISHING_MODES = ("vehicle", "monitor")


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from an untracked ``.env`` in the project folder into
    the environment. Real environment variables win. Holds the API keys, the
    broker password and the admin passphrase hash, which are kept out of the
    repository."""
    path = path or PROJECT_DIR / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    path: Path
    # the file was there and read
    found: bool = False
    # what's wrong in the file, one line each: a table or entry that can't be
    # used (its defaults apply, or it's left out) or an unknown name (skipped)
    problems: tuple[str, ...] = ()
    # the whole file couldn't be read (a TOML error): every default applies
    unread: bool = False
    mqtt: MQTTSettings = field(default_factory=MQTTSettings)
    # the login VIEWER uses: [mqtt.viewer] on top of [mqtt], always read-only
    viewer_mqtt: MQTTSettings = field(default_factory=lambda: MQTTSettings(read_only=True))
    # data providers turned on with [providers.<name>] enabled = true
    providers: frozenset[str] = frozenset()
    # the code providers turned on, in file order
    code_providers: tuple["CodeProvider", ...] = ()
    archive: "ArchiveSettings" = field(default_factory=lambda: ArchiveSettings())
    # vehicle logger-file layouts, (name, columns and file pattern) in file
    # order: live obs files and ARCHIVE's vehicle-obs files
    obs_layouts: tuple[tuple[str, FieldMap], ...] = (("default", FieldMap()),)
    # [[sources]]: data a team adds without code (data/sources.py)
    sources: tuple[Source, ...] = ()
    # [[auth]]: a key or CA for the URLs below a server or folder (data/auth.py)
    auth: tuple[Auth, ...] = ()
    # [map]: home location and the map's starting view
    map: "MapSettings" = field(default_factory=lambda: MapSettings())
    # [units]: how temperatures and distances are shown
    units: "UnitsSettings" = field(default_factory=lambda: UnitsSettings())

    @property
    def error(self) -> str:
        """The problems, one per line; "" for a file with none."""
        return "\n".join(self.problems)

    def sources_of(self, kind: str) -> tuple[Source, ...]:
        """The [[sources]] of *kind*, then the code providers' ones."""
        from settings import code_providers  # noqa: PLC0415 - loads them on first use
        return tuple(s for s in self.sources + code_providers.code_sources(self) if s.kind == kind)

    @property
    def obs_columns(self) -> FieldMap:
        """The first logger layout: ARCHIVE's vehicle-obs files use it."""
        return self.obs_layouts[0][1]

    def obs_layout(self, name: str = "") -> FieldMap:
        """The logger layout *name*, else the first."""
        return pick_layout(self.obs_layouts, name)

    def mqtt_for(self, viewer: bool) -> MQTTSettings:
        return self.viewer_mqtt if viewer else self.mqtt

    def broker_problems(self, mode: str) -> list[str]:
        """Why *mode* can't launch with this broker setup; empty when it can.
        VIEWER always launches (without a broker it just gets no live data)
        and ARCHIVE never connects to a broker."""
        if mode not in _PUBLISHING_MODES:
            return []
        s = self.mqtt
        if not s.configured:
            where = self.path.name if self.found else f"{self.path.name} (not found)"
            return [f"no broker is set: add an [mqtt] host to {where}"]
        if s.read_only:
            return ["[mqtt] read_only is on, so this login can't publish"]
        return [f"missing file: {p}" for p in s.missing_files()]


_PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class CodeProvider:
    """A provider: a package at providers/<name>/ (git-ignored except NSSL's)
    or at [providers.<name>] path. Its interface is in docs/providers.md."""
    name: str
    path: Path
    # the table's other keys, handed to the package's functions
    options: tuple[tuple[str, object], ...] = ()
    # loaded without a [providers.<name>] table (BUILTIN_PROVIDERS): its
    # archive drawer and hooks are there, but it isn't one of the team's
    # default history / vehicle_obs sources
    builtin: bool = False

    def options_dict(self) -> dict:
        return dict(self.options)


@dataclass(frozen=True)
class ArchiveSettings:
    """[archive]: where the recorder writes and where ARCHIVE reads recorded
    broker traffic (archive/history.py, archive/recorder.py)."""
    # folder the recorder writes to
    recordings: Path = PROJECT_DIR / "recordings"
    # history sources in the order tried: a folder, an http(s) URL root or a
    # provider name; None means the default (archive/history.py)
    history: tuple[str, ...] | None = None
    # file-name prefix the folder and URL sources read; "" means the topic root's
    history_prefix: str = ""
    # vehicle-obs sources in the order tried for each vehicle: a folder, an
    # http(s) URL root or a provider name; None means the providers that are on
    # (archive/fetchers/vehicle_obs_archive_fetcher.py)
    vehicle_obs: tuple[str, ...] | None = None
    # where a vehicle's file for a day sits below a folder or URL root
    vehicle_obs_path: str = "{vehicle}/{date}.csv"
    # a vehicle's broker ID -> the name its vehicle-obs files use ("p1" -> "probe1")
    vehicle_aliases: tuple[tuple[str, str], ...] = ()
    # field campaigns for the case finder: (name, years)
    campaigns: tuple[tuple[str, tuple[int, ...]], ...] = ()
    # the session window (archive/session.py): hour UTC it starts on its
    # date, its longest length, and how long after the start activity begins
    # to count toward where the timeline ends
    session_start_utc: int = 0
    session_hours: int = 30
    session_activity_after_hours: int = 12

    def file_vehicle(self, vehicle_id: str) -> str:
        """The name a vehicle's files use (vehicle_aliases), else its broker ID."""
        return dict(self.vehicle_aliases).get(vehicle_id.strip().lower(), vehicle_id)

    def broker_vehicle(self, file_name: str) -> str:
        """The broker ID for a name the files use, else the name."""
        return {f: v for v, f in self.vehicle_aliases}.get(file_name.strip().lower(), file_name)


@dataclass(frozen=True)
class MapSettings:
    """[map]: where the map opens, and the fallback position when no vehicle
    has one (radar site choice, routing, the command palette)."""
    # the middle of the contiguous US
    home_lat: float = 39.5
    home_lon: float = -98.35
    zoom: float = 4
    # IANA zones for ARCHIVE's local clocks; empty: the computer's own zone
    time_zones: tuple[str, ...] = ()
    # past deployment locations for PREV LOCS: a CSV file path or http(s) URL; "" hides it
    deployment_locations: str = ""


@dataclass(frozen=True)
class UnitsSettings:
    """[units]: display units (core/units.py). Data stay in °C, m/s and km;
    wind is always shown in knots."""
    temperature: str = "F"      # "F" or "C"
    distance: str = "mi"        # "mi" or "km"


def file_prefix(topic_root: str) -> str:
    """A topic root as recorded file names use it: levels joined with dots."""
    return topic_root.replace("/", ".")


class SettingsError(ValueError):
    """Raised when a table outside [mqtt] is invalid."""


# providers that ship with STORM and load without a [providers.<name>]
# table, since their data is public (NSSL's THREDDS archive);
# [providers.<name>] enabled = false leaves one out
BUILTIN_PROVIDERS = ("nssl",)


def _parse_providers(table, warn: Warn | None = None) -> tuple[frozenset[str], tuple[CodeProvider, ...]]:
    """(the providers turned on, the code providers among them, then the
    built-in ones storm.toml doesn't mention). With *warn*, a provider whose
    table is invalid is a warning and left out (a built-in one too)."""
    if not isinstance(table, dict):
        raise SettingsError("[providers] must be a table")
    enabled, code = set(), []
    for name, sub in table.items():
        try:
            found = _parse_provider(name, sub)
        except SettingsError as e:
            if warn is None:
                raise
            warn(f"{e} (provider left out)")
            continue
        if found is not None:
            enabled.add(name)
            code.append(found)
    for name in BUILTIN_PROVIDERS:
        path = PROJECT_DIR / "providers" / name
        if name not in table and (path / "__init__.py").is_file():
            code.append(CodeProvider(name, path, builtin=True))
    return frozenset(enabled), tuple(code)


def _parse_provider(name: str, sub) -> CodeProvider | None:
    """[providers.<name>]'s provider, or None when it's off."""
    if not isinstance(sub, dict) or not isinstance(sub.get("enabled", False), bool):
        raise SettingsError(f"[providers.{name}] enabled must be true or false")
    if not _PROVIDER_NAME_RE.match(name):
        raise SettingsError(f"[providers.{name}]: a provider name is lower-case letters, digits "
                            "and _, starting with a letter")
    if not sub.get("enabled", False):
        return None
    path = PROJECT_DIR / "providers" / name
    if "path" in sub:
        raw = sub["path"]
        if not isinstance(raw, str) or not raw.strip():
            raise SettingsError(f"[providers.{name}] path must be a folder path")
        path = Path(raw.strip()).expanduser()
        path = path if path.is_absolute() else PROJECT_DIR / path
    if not (path / "__init__.py").is_file():
        hint = did_you_mean(name, (p.parent.name for p in (PROJECT_DIR / "providers").glob("*/__init__.py")))
        raise SettingsError(f"[providers.{name}]: no provider package at {path}{hint} "
                            "(a folder with an __init__.py; see docs/providers.md)")
    options = tuple((k, v) for k, v in sub.items() if k not in ("enabled", "path"))
    return CodeProvider(name, path, options)


def is_provider_entry(entry: str) -> bool:
    """Whether an [archive] history / vehicle_obs entry names a provider:
    one that's on, or a package in providers/ that's off (skipped)."""
    return entry in SETTINGS.providers or bool(
        _PROVIDER_NAME_RE.match(entry) and (PROJECT_DIR / "providers" / entry / "__init__.py").is_file())


def _source_list(table, key: str) -> tuple[str, ...]:
    raw = table[key]
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise SettingsError(f"[archive] {key} must be a list of folders, URLs or provider names")
    return tuple(x.strip() for x in raw)


def _parse_vehicle_aliases(raw) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict) or not all(isinstance(v, str) and v.strip() for v in raw.values()):
        raise SettingsError('[archive] vehicle_aliases must map broker IDs to file names, such as { p1 = "probe1" }')
    out = tuple((k.strip().lower(), v.strip().lower()) for k, v in raw.items())
    if len({f for _, f in out}) != len(out):
        raise SettingsError("[archive] vehicle_aliases: two vehicles can't share a file name")
    return out


def _parse_campaigns(raw, warn: Warn | None = None) -> tuple[tuple[str, tuple[int, ...]], ...]:
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        raise SettingsError("[[archive.campaigns]] must be tables with a name and years")
    out = []
    for entry in raw:
        entry = check_keys(entry, ("name", "years"), "[[archive.campaigns]]", SettingsError, warn)
        name, years = entry.get("name"), entry.get("years")
        if not isinstance(name, str) or not name.strip():
            raise SettingsError("[[archive.campaigns]] name must be a non-empty string")
        if (not isinstance(years, list) or not years
                or not all(isinstance(y, int) and not isinstance(y, bool) and 1900 <= y <= 2200 for y in years)):
            raise SettingsError(f"[[archive.campaigns]] {name}: years must be a list of years such as [2024, 2025]")
        out.append((name.strip(), tuple(sorted(set(years)))))
    return tuple(out)


def _parse_archive(table, warn: Warn | None = None) -> ArchiveSettings:
    if not isinstance(table, dict):
        raise SettingsError("[archive] must be a table")
    table = check_keys(table, ("recordings", "history", "history_prefix",
                               "vehicle_obs", "vehicle_obs_path", "vehicle_aliases", "campaigns",
                               "session_start_utc", "session_hours", "session_activity_after_hours"),
                       "[archive]", SettingsError, warn)
    out = ArchiveSettings()
    if "recordings" in table:
        raw = table["recordings"]
        if not isinstance(raw, str) or not raw.strip():
            raise SettingsError("[archive] recordings must be a folder path")
        path = Path(raw.strip()).expanduser()
        out = replace(out, recordings=path if path.is_absolute() else PROJECT_DIR / path)
    if "history" in table:
        out = replace(out, history=_source_list(table, "history"))
    if "history_prefix" in table:
        raw = table["history_prefix"]
        if not isinstance(raw, str) or not raw.strip() or "/" in raw or "\\" in raw:
            raise SettingsError("[archive] history_prefix must be a file-name prefix such as \"storm\"")
        out = replace(out, history_prefix=raw.strip())
    if "vehicle_obs" in table:
        out = replace(out, vehicle_obs=_source_list(table, "vehicle_obs"))
    if "vehicle_obs_path" in table:
        raw = table["vehicle_obs_path"]
        if (not isinstance(raw, str) or "{vehicle}" not in raw or "{date}" not in raw
                or raw.strip().startswith("/") or ".." in raw.split("/")):
            raise SettingsError("[archive] vehicle_obs_path must be a relative path with {vehicle} and {date}, "
                                "such as \"{vehicle}/{date}.csv\"")
        try:
            raw.format(vehicle="v", date="20260101")
        except (KeyError, IndexError, ValueError):
            raise SettingsError("[archive] vehicle_obs_path may only use {vehicle} and {date}") from None
        out = replace(out, vehicle_obs_path=raw.strip())
    if "vehicle_aliases" in table:
        out = replace(out, vehicle_aliases=_parse_vehicle_aliases(table["vehicle_aliases"]))
    if "campaigns" in table:
        out = replace(out, campaigns=_parse_campaigns(table["campaigns"], warn))
    for key, low, high in (("session_start_utc", 0, 23), ("session_hours", 1, 48),
                           ("session_activity_after_hours", 0, 47)):
        if key in table:
            raw = table[key]
            if isinstance(raw, bool) or not isinstance(raw, int) or not low <= raw <= high:
                raise SettingsError(f"[archive] {key} must be a whole number from {low} to {high}")
            out = replace(out, **{key: raw})
    # a session reads files for its own date and the next one only
    if out.session_start_utc + out.session_hours > 48:
        raise SettingsError("[archive] session_start_utc + session_hours must be at most 48 "
                            "(a session ends by the end of the next UTC day)")
    if out.session_activity_after_hours >= out.session_hours:
        raise SettingsError("[archive] session_activity_after_hours must be less than session_hours")
    return out


def _number(table, key: str, low: float, high: float, name: str = "map") -> float:
    raw = table[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not low <= raw <= high:
        raise SettingsError(f"[{name}] {key} must be a number from {low:g} to {high:g}")
    return float(raw)


def _parse_map(table, warn: Warn | None = None) -> MapSettings:
    if not isinstance(table, dict):
        raise SettingsError("[map] must be a table")
    table = check_keys(table, ("home_lat", "home_lon", "zoom", "time_zones", "deployment_locations"),
                       "[map]", SettingsError, warn)
    if ("home_lat" in table) != ("home_lon" in table):
        raise SettingsError("[map] home_lat and home_lon go together")
    out = MapSettings()
    for key, low, high in (("home_lat", -90, 90), ("home_lon", -180, 180), ("zoom", 1, 18)):
        if key in table:
            out = replace(out, **{key: _number(table, key, low, high)})
    if "time_zones" in table:
        out = replace(out, time_zones=_parse_time_zones(table["time_zones"]))
    if "deployment_locations" in table:
        raw = table["deployment_locations"]
        if not isinstance(raw, str):
            raise SettingsError("[map] deployment_locations must be a CSV file path or an http(s) URL")
        raw = raw.strip()
        if raw and not raw.startswith(("http://", "https://")):
            path = Path(raw).expanduser()
            raw = str(path if path.is_absolute() else PROJECT_DIR / path)
        out = replace(out, deployment_locations=raw)
    return out


def _parse_units(table, warn: Warn | None = None) -> UnitsSettings:
    if not isinstance(table, dict):
        raise SettingsError("[units] must be a table")
    table = check_keys(table, ("temperature", "distance"), "[units]", SettingsError, warn)
    out = UnitsSettings()
    for key, allowed in (("temperature", ("F", "C")), ("distance", ("mi", "km"))):
        if key in table:
            if table[key] not in allowed:
                raise SettingsError(f"[units] {key} must be " + " or ".join(f'"{a}"' for a in allowed))
            out = replace(out, **{key: table[key]})
    return out


def _parse_time_zones(raw) -> tuple[str, ...]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if not isinstance(raw, list) or not all(isinstance(z, str) and z.strip() for z in raw):
        raise SettingsError('[map] time_zones must be a list of zone names, such as ["America/Chicago"]')
    if len(raw) > 3:
        raise SettingsError("[map] time_zones takes at most 3 zones")
    for name in raw:
        try:
            ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingsError(f"[map] time_zones: unknown zone {name.strip()!r}") from None
    return tuple(z.strip() for z in raw)


def config_path() -> Path:
    raw = os.environ.get("STORM_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else PROJECT_DIR / "storm.toml"


# the top-level tables storm.toml may have ([[sources]] and [[auth]] are arrays of tables)
_TABLES = frozenset({"mqtt", "providers", "archive", "obs_columns", "sources", "auth", "map", "units"})


def load(path: Path | None = None) -> Settings:
    """Read *path* (default: config_path()).  A missing file gives the
    defaults (no broker); one that isn't TOML gives the defaults plus
    ``problems``.  Otherwise each table stands alone: an invalid one keeps
    its defaults ([[sources]], [[auth]], [providers.<name>] and named
    [obs_columns] layouts: that entry is left out), an unknown table or key
    is skipped, and each is a line in ``problems``.  Relative paths in the
    file resolve against the project folder."""
    path = path or config_path()
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        return Settings(path=path)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return Settings(path=path, problems=(f"{path.name}: {e}",), unread=True)

    problems: list[str] = []
    warn = problems.append

    def part(parse, default, consequence):
        """parse(), or *default* with the problem noted."""
        try:
            return parse()
        except (MQTTSettingsError, SettingsError, ObsColumnsError, SourceError, AuthError) as e:
            warn(f"{e} ({consequence})")
            return default

    # a misspelled table ([unit]) would otherwise leave its settings at the defaults unnoticed
    for name in sorted(set(raw) - _TABLES):
        warn(f"unknown table [{name}]{did_you_mean(name, _TABLES)} (skipped; the tables are "
             + ", ".join(f"[{t}]" for t in sorted(_TABLES)) + ")")

    table = raw.get("mqtt", {})
    mqtt = part(lambda: parse_mqtt(table, PROJECT_DIR, warn=warn), MQTTSettings(), "no broker until it's fixed")
    viewer_table = table.get("viewer", {}) if isinstance(table, dict) else {}
    if isinstance(table, dict):
        for name in sorted(k for k, v in table.items() if isinstance(v, dict) and k != "viewer"):
            warn(f"unknown table [mqtt.{name}]{did_you_mean(name, ('viewer',))} (skipped)")

    def parse_viewer():
        # one team, one set of topics: VIEWER reads what the others publish
        for key in ("topic_root", "daily_reset_utc", "shared_items_url"):
            if isinstance(viewer_table, dict) and key in viewer_table:
                raise MQTTSettingsError(f"[mqtt.viewer] can't set {key}; it uses [mqtt]'s")
        return parse_mqtt(viewer_table, PROJECT_DIR, inherit=mqtt,
                          password_env=VIEWER_PASSWORD_ENV, name="mqtt.viewer", warn=warn)
    viewer = part(parse_viewer, mqtt, "VIEWER uses [mqtt]'s login, read-only")

    providers, code_providers = part(lambda: _parse_providers(raw.get("providers", {}), warn),
                                     _parse_providers({}), "only the built-in providers load")
    archive = part(lambda: _parse_archive(raw.get("archive", {}), warn), ArchiveSettings(),
                   "[archive] uses its defaults")
    obs_layouts = part(lambda: parse_obs_layouts(raw.get("obs_columns", {}), warn=warn),
                       (("default", FieldMap()),), "[obs_columns] uses the default layout")
    sources = part(lambda: parse_sources(raw.get("sources", []), PROJECT_DIR, warn), (),
                   "no [[sources]] load")
    auth = part(lambda: parse_auth(raw.get("auth", []), PROJECT_DIR, warn), (), "no [[auth]] tables load")
    map_settings = part(lambda: _parse_map(raw.get("map", {}), warn), MapSettings(), "[map] uses its defaults")
    units = part(lambda: _parse_units(raw.get("units", {}), warn), UnitsSettings(), "[units] uses its defaults")
    return Settings(path=path, found=True, problems=tuple(problems), mqtt=mqtt,
                    viewer_mqtt=replace(viewer, read_only=True), providers=providers,
                    code_providers=code_providers,
                    archive=archive, obs_layouts=obs_layouts, sources=sources, auth=auth,
                    map=map_settings, units=units)


load_dotenv()
SETTINGS: Settings = load()
