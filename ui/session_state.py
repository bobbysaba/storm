"""Last-session view, saved per launch mode (QSettings ``session/<mode>``).

Each control's ``save_state()`` dict is stored as JSON under the mode it ran
in, so ARCHIVE never inherits live layers and VEHICLE never inherits MONITOR's.
Settings that already persist on their own (window geometry, pins, drawer mode,
text scale, layer order, NWS filters) are not repeated here.
"""
import json
import logging

from PyQt6.QtCore import QSettings

log = logging.getLogger(__name__)

VEHICLE, MONITOR, VIEWER, ARCHIVE = "vehicle", "monitor", "viewer", "archive"
MODES = (VEHICLE, MONITOR, VIEWER, ARCHIVE)

_PREFIX = "session/"


def mode_key(monitor: bool = False, viewer: bool = False, archive: bool = False) -> str:
    if archive:
        return ARCHIVE
    if monitor:
        return MONITOR
    if viewer:
        return VIEWER
    return VEHICLE


def _settings(settings: QSettings | None) -> QSettings:
    return settings if settings is not None else QSettings("NSSL", "STORM")


def load(mode: str, settings: QSettings | None = None) -> dict:
    raw = _settings(settings).value(_PREFIX + mode, "", type=str)
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except ValueError:
        log.warning("ignoring unreadable saved session for %s", mode)
        return {}
    return state if isinstance(state, dict) else {}


def save(mode: str, state: dict, settings: QSettings | None = None) -> None:
    _settings(settings).setValue(_PREFIX + mode, json.dumps(state, sort_keys=True))


def clear(mode: str, settings: QSettings | None = None) -> None:
    _settings(settings).remove(_PREFIX + mode)
