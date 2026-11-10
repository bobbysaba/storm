import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QLabel

from ui import text_scale


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "storm.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def scaled(app, settings):
    text_scale.install(app, settings)
    yield settings
    # leave the process-wide hook at 100 % for other tests
    text_scale.set_percent(100, settings)


def test_scale_sheet_rounds_each_size():
    sheet = "QLabel { font-size: 11px; } QToolButton { font-size:12px; padding: 3px; }"
    assert text_scale.scale_sheet(sheet, 100) == sheet
    assert text_scale.scale_sheet(sheet, 130) == (
        "QLabel { font-size: 14px; } QToolButton { font-size:16px; padding: 3px; }"
    )
    assert text_scale.scale_sheet("font-size: 11px;", 115) == "font-size: 13px;"


def test_saved_percent_rejects_unknown_values(settings):
    assert text_scale.saved_percent(settings) == 100
    settings.setValue("ui/text_scale", 175)
    assert text_scale.saved_percent(settings) == 100
    settings.setValue("ui/text_scale", "junk")
    assert text_scale.saved_percent(settings) == 100
    settings.setValue("ui/text_scale", 130)
    assert text_scale.saved_percent(settings) == 130


def test_set_percent_rescales_existing_and_new_sheets(scaled):
    early = QLabel("early")
    early.setStyleSheet("color: red; font-size: 10px;")
    plain = QLabel("plain")
    plain.setStyleSheet("color: red;")

    assert text_scale.set_percent(130, scaled)
    assert early.styleSheet() == "color: red; font-size: 13px;"
    assert plain.styleSheet() == "color: red;"

    late = QLabel("late")
    late.setStyleSheet("font-size: 12px;")
    assert late.styleSheet() == "font-size: 16px;"

    # back to 100 % restores the original sizes, not a rescaled copy
    assert text_scale.set_percent(100, scaled)
    assert early.styleSheet() == "color: red; font-size: 10px;"
    assert late.styleSheet() == "font-size: 12px;"
    assert not text_scale.set_percent(100, scaled)


def test_set_percent_saves_and_scales_app_font(app, scaled):
    base = app.font().pointSizeF()
    text_scale.set_percent(115, scaled)
    assert scaled.value("ui/text_scale", type=int) == 115
    assert app.font().pointSizeF() == pytest.approx(base * 1.15)


def test_replacing_sheet_without_font_size_drops_the_base(scaled):
    label = QLabel()
    label.setStyleSheet("font-size: 10px;")
    label.setStyleSheet("color: blue;")
    text_scale.set_percent(130, scaled)
    assert label.styleSheet() == "color: blue;"
