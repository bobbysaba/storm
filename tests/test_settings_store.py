import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings

from ui import settings_store


def _ini(tmp_path, name):
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def test_copies_missing_keys_once(tmp_path):
    store, legacy = _ini(tmp_path, "storm.ini"), _ini(tmp_path, "nssl.ini")
    legacy.setValue("layers/order", ["radar", "surface"])
    legacy.setValue("ui/text_scale", 115)
    store.setValue("ui/text_scale", 130)

    assert settings_store.copy_legacy(store, legacy) == 1
    assert store.value("layers/order") == ["radar", "surface"]
    assert int(store.value("ui/text_scale")) == 130     # already set: kept

    legacy.setValue("drawers/open", "map")
    assert settings_store.copy_legacy(store, legacy) == 0
    assert not store.contains("drawers/open")


def test_empty_legacy_store(tmp_path):
    store = _ini(tmp_path, "storm.ini")
    assert settings_store.copy_legacy(store, _ini(tmp_path, "nssl.ini")) == 0
    assert store.value("settings/legacy_copied", False, type=bool)
