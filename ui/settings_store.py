"""STORM's saved UI settings are one QSettings store: QSettings() with the
organization and application names main.py sets ("STORM" / "STORM").

Before 2026-11 most of them were in a separate ("NSSL", "STORM") store;
copy_legacy() copies those over once, so a layer order or drawer layout
saved there carries over.
"""

import logging

from PyQt6.QtCore import QSettings

log = logging.getLogger(__name__)

LEGACY_ORG, LEGACY_APP = "NSSL", "STORM"
_DONE_KEY = "settings/legacy_copied"


def copy_legacy(store: QSettings | None = None, legacy: QSettings | None = None) -> int:
    """Copy keys from the old ("NSSL", "STORM") store that *store* doesn't
    have yet, once. Returns how many were copied."""
    store = store if store is not None else QSettings()
    if store.value(_DONE_KEY, False, type=bool):
        return 0
    legacy = legacy if legacy is not None else QSettings(LEGACY_ORG, LEGACY_APP)
    copied = 0
    for key in legacy.allKeys():
        if not store.contains(key):
            store.setValue(key, legacy.value(key))
            copied += 1
    store.setValue(_DONE_KEY, True)
    store.sync()
    if copied:
        log.info("copied %d saved settings from the old NSSL/STORM store", copied)
    return copied
