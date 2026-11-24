"""Loads the providers in storm.toml: a package at providers/<name>/
(git-ignored, except NSSL's, which ships with STORM) or at
``[providers.<name>] path``. Its package may define, each taking the
options dict (the table's other keys):

    sources(options)             -> list of data.sources.code_source(...):
                                    station_obs, overlay and sounding sources
                                    whose fetch() STORM calls in place of a URL
    history_source(options)      -> an archive/history.py HistorySource
                                    ([archive] history lists it by name)
    vehicle_obs_source(options)  -> an archive/vehicle_obs.py VehicleObsSource
                                    ([archive] vehicle_obs lists it by name)

the instrument hooks (soundings, wind profiles, surface playback,
lidar), which first_hook() asks the providers for in turn, and
archive_drawer(), a provider's own ARCHIVE button and drawer
(ui/app/main_window_provider_drawers.py), asked of every provider.
Keys, CAs, shared items, deployment locations and contour layers are
settings ([[auth]], [mqtt] shared_items_url, [map] deployment_locations,
kind = "contours" [[sources]]), not hooks.
docs/providers.md has the details and an example. A provider that fails to
load or returns bad sources is logged and left out; the app still starts.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import replace
from pathlib import Path

log = logging.getLogger(__name__)

PROVIDERS_DIR = Path(__file__).resolve().parent.parent / "providers"

# name -> module, or None when it failed to load
_modules: dict[str, object] = {}
# (name, path) -> its sources
_sources: dict[tuple[str, Path], tuple] = {}
# name -> why it failed (also logged)
errors: dict[str, str] = {}
# (name, path) -> its AUTH tables, checked
_auth: dict[tuple[str, Path], tuple] = {}


def load(provider):
    """A settings.CodeProvider's package, imported once; None when it
    failed to import."""
    key = f"{provider.name}@{provider.path}"
    if key in _modules:
        return _modules[key]
    module = None
    try:
        if provider.path.resolve() == (PROVIDERS_DIR / provider.name).resolve():
            module = importlib.import_module(f"providers.{provider.name}")
        else:
            name = f"storm_provider_{provider.name}"
            spec = importlib.util.spec_from_file_location(
                name, provider.path / "__init__.py", submodule_search_locations=[str(provider.path)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken provider mustn't stop the app
        sys.modules.pop(f"storm_provider_{provider.name}", None)
        errors[provider.name] = f"failed to load: {type(exc).__name__}: {exc}"
        log.exception("provider %s failed to load from %s", provider.name, provider.path)
        module = None
    _modules[key] = module
    return module


def hook(provider, name: str, **kwargs):
    """What the package's *name*(options, **kwargs) returns, or None when it
    has no such function or the call failed."""
    module = load(provider)
    fn = getattr(module, name, None) if module is not None else None
    if not callable(fn):
        return None
    try:
        return fn(provider.options_dict(), **kwargs)
    except Exception as exc:  # noqa: BLE001
        errors[provider.name] = f"{name}() failed: {type(exc).__name__}: {exc}"
        log.exception("provider %s: %s() failed", provider.name, name)
        return None


def auth_tables(settings) -> tuple:
    """The [[auth]]-style tables the code providers in *settings* declare in
    a module-level AUTH list (NSSL's: the CA its THREDDS server leaves out
    of its chain), for URLs no storm.toml [[auth]] table covers. A list
    that doesn't check is logged and left out."""
    from data.auth import AuthError, parse_auth  # noqa: PLC0415
    out = []
    for provider in settings.code_providers:
        key = (provider.name, provider.path)
        if key not in _auth:
            module = load(provider)
            raw = getattr(module, "AUTH", None) if module is not None else None
            try:
                _auth[key] = parse_auth(raw, provider.path) if raw else ()
            except AuthError as exc:
                log.warning("provider %s: AUTH: %s; ignored", provider.name, exc)
                _auth[key] = ()
        out.extend(_auth[key])
    return tuple(out)


def team_vehicle_renames(settings) -> dict[str, str]:
    """Vehicle renames for the team's own recorded traffic: the
    VEHICLE_RENAMES of providers marked as the team ([providers.<name>]
    team = true; NSSL's profile), lower-case IDs as recorded -> as shown."""
    out: dict[str, str] = {}
    for provider in settings.code_providers:
        if provider.options_dict().get("team") is True:
            module = load(provider)
            renames = getattr(module, "VEHICLE_RENAMES", None) if module is not None else None
            if isinstance(renames, dict):
                out.update({str(k).lower(): str(v) for k, v in renames.items()})
    return out


def code_sources(settings) -> tuple:
    """The sources the code providers in *settings* add, checked: each must
    be a code_source() with an id no [[sources]] table or earlier provider
    uses. Asked once per provider."""
    from data.sources import Source  # noqa: PLC0415
    taken = {s.id for s in settings.sources}
    out = []
    for provider in settings.code_providers:
        key = (provider.name, provider.path)
        if key not in _sources:
            found = []
            for source in hook(provider, "sources") or ():
                if not isinstance(source, Source) or source.fetch is None:
                    log.warning("provider %s: sources() returned %r, not a code_source()",
                                provider.name, source)
                    continue
                found.append(replace(source, provider=provider.name))
            _sources[key] = tuple(found)
        for source in _sources[key]:
            if source.id in taken:
                log.warning("provider %s: source id %r is already used; skipped", provider.name, source.id)
                continue
            taken.add(source.id)
            out.append(source)
    return tuple(out)


def archive_hook(settings, name: str, hook_name: str):
    """What provider *name*'s history_source / vehicle_obs_source returns,
    or None when that provider is off or has no such hook."""
    provider = code_provider(settings, name)
    return None if provider is None else hook(provider, hook_name)


def first_hook(settings, hook_name: str, **kwargs):
    """(provider, result) for the first provider in *settings*, in file
    order, whose *hook_name* returns something; None when none does. A
    provider whose hook returns None (an API without its key, say) passes
    the turn to the next."""
    for provider in settings.code_providers:
        result = hook(provider, hook_name, **kwargs)
        if result is not None:
            return provider, result
    return None


def every_hook(settings, hook_name: str, **kwargs) -> list:
    """(provider, result) for every provider in *settings*, in file order,
    whose *hook_name* returns something (archive_drawer: one button each)."""
    out = []
    for provider in settings.code_providers:
        result = hook(provider, hook_name, **kwargs)
        if result is not None:
            out.append((provider, result))
    return out


def first_result(settings, hook_name: str, **kwargs):
    """What first_hook() found, without the provider; None when nothing."""
    found = first_hook(settings, hook_name, **kwargs)
    return None if found is None else found[1]


def provided(hook_name: str, **kwargs):
    """first_result() for the settings in use: what the UI asks for."""
    import settings  # noqa: PLC0415
    return first_result(settings.SETTINGS, hook_name, **kwargs)


def label(provider) -> str:
    """A provider's name on buttons: its package's LABEL, else its name in
    capitals."""
    module = load(provider)
    return str(getattr(module, "LABEL", "") or provider.name.upper())


def code_provider(settings, name: str):
    """The settings.CodeProvider called *name*, or None."""
    return next((p for p in settings.code_providers if p.name == name), None)
