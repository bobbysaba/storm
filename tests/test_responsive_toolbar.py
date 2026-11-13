import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from ui import text_scale
from ui.theme import DARK_THEME
from ui.widgets.responsive_toolbar import FULL, ICONS, OVERFLOW, ResponsiveToolbar, pick_tier


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


_hosts = []


def _build(meso=True, sfcoa=True, archive=False):
    """Same buttons and groups as MainWindow._init_toolbar, themed like the window."""
    host = QWidget()
    host.setStyleSheet(DARK_THEME)
    _hosts.append(host)
    tb = ResponsiveToolbar(host)
    b = {}
    b["radar"] = tb.add_button("RADAR", "radar", "radar")
    b["satellite"] = tb.add_button("SATELLITE", "satellite", "satellite")
    b["hazards"] = tb.add_button("HAZARDS", "hazards", "hazards")
    b["surface"] = tb.add_button("SURFACE", "surface", "surface")
    if meso:
        b["meso"] = tb.add_button("MESO", "meso", "meso", overflow=True)
    if sfcoa:
        b["sfcoa"] = tb.add_button("SFCOA", "sfcoa", "sfcoa", overflow=True)
    tb.add_group()
    b["sounding"] = tb.add_button("SOUNDING", "sounding", "sounding")
    tb.add_group()
    b["vehicles"] = tb.add_button("VEHICLES", "vehicles", "vehicles")
    b["prev_locs"] = tb.add_button("PREV LOCS", "prev", "prev_locs", overflow=True)
    b["annotate"] = tb.add_button("ANNOTATE", "annotate", "annotate")
    tb.add_group()
    b["map"] = tb.add_button("MAP", "map", "map")
    if archive:
        for key in ("prev_locs", "surface", "annotate", "meso", "sfcoa"):
            if key in b:
                tb.set_available(b[key], False)
    tb.finish()
    return tb, b


def test_pick_tier():
    widths = {FULL: 900, ICONS: 500, OVERFLOW: 400}
    assert pick_tier(widths, 1000) == FULL
    assert pick_tier(widths, 900) == FULL
    assert pick_tier(widths, 899) == ICONS
    assert pick_tier(widths, 450) == OVERFLOW
    assert pick_tier(widths, 100) == OVERFLOW
    # nothing to overflow: icon-only is the narrowest tier
    assert pick_tier({FULL: 900, ICONS: 500}, 100) == ICONS


def test_widths_shrink_by_tier(app):
    tb, _ = _build()
    w = tb.measured_widths()
    assert w[FULL] > w[ICONS] > w[OVERFLOW]


def test_fit_switches_tiers(app):
    tb, b = _build()
    w = tb.measured_widths()

    assert tb.fit(w[FULL]) == FULL
    assert b["radar"].toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert tb.more_button.isHidden()

    assert tb.fit(w[FULL] - 1) == ICONS
    assert b["radar"].toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not b["meso"].isHidden()

    seen = []
    tb.overflow_changed.connect(seen.append)
    assert tb.fit(w[ICONS] - 1) == OVERFLOW
    assert b["meso"].isHidden() and b["sfcoa"].isHidden() and b["prev_locs"].isHidden()
    assert not b["radar"].isHidden()
    assert not tb.more_button.isHidden()
    assert seen == [{b["meso"], b["sfcoa"], b["prev_locs"]}]

    # widening brings them back
    assert tb.fit(w[FULL] + 50) == FULL
    assert not b["meso"].isHidden() and tb.more_button.isHidden()
    assert seen[-1] == set()


def test_label_in_tooltip(app):
    _, b = _build()
    assert b["radar"].toolTip().startswith("RADAR:")
    assert b["prev_locs"].toolTip().startswith("PREV LOCS:")


def test_unavailable_buttons_stay_hidden(app):
    tb, b = _build(archive=True)
    w = tb.measured_widths()
    for tier_w in (w[FULL], w[ICONS] - 1, 10):
        tb.fit(tier_w)
        for key in ("prev_locs", "surface", "annotate", "meso", "sfcoa"):
            assert b[key].isHidden()
    # archive has nothing left to overflow
    assert OVERFLOW not in w
    assert tb.more_button.isHidden()


def test_empty_group_drops_its_separator(app):
    tb, b = _build(meso=False, sfcoa=False)
    tb.set_available(b["vehicles"], False)
    tb.set_available(b["prev_locs"], False)
    tb.set_available(b["annotate"], False)
    tb.fit(10_000)
    visible_seps = [s for s in tb._separators if not s.isHidden()]
    # data | sounding | map
    assert len(visible_seps) == 2


def test_overflow_menu_clicks_real_button(app):
    tb, b = _build()
    clicks = []
    b["meso"].clicked.connect(lambda: clicks.append("meso"))
    tb.fit(tb.measured_widths()[ICONS] - 1)
    tb._rebuild_menu()
    actions = {a.text(): a for a in tb._more_menu.actions()}
    assert set(actions) == {"MESO", "SFCOA", "PREV LOCS"}
    actions["MESO"].trigger()
    assert clicks == ["meso"]
    assert b["meso"].isChecked()
    tb._rebuild_menu()
    assert {a.text(): a for a in tb._more_menu.actions()}["MESO"].isChecked()


@pytest.mark.parametrize("meso,sfcoa", [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize("archive", [False, True])
def test_fits_1024_at_largest_text_scale(app, tmp_path, meso, sfcoa, archive):
    """The narrowest tier fits a 1024px window (8px margin each side) at 130 %."""
    settings = QSettings(str(tmp_path / "storm.ini"), QSettings.Format.IniFormat)
    text_scale.install(app, settings)
    text_scale.set_percent(130, settings)
    try:
        tb, _ = _build(meso, sfcoa, archive)
        tb.set_icon_px(round(14 * 1.3))
        assert min(tb.measured_widths().values()) <= 1024 - 16
    finally:
        text_scale.set_percent(100, settings)


def test_overflow_menu_pins(app):
    tb, b = _build()
    pinned = {b["prev_locs"]}
    calls = []

    def set_pinned(btn, on):
        calls.append((btn.text(), on))
        (pinned.add if on else pinned.discard)(btn)

    # SFCOA has no drawer entry here, so it gets no pin toggle
    tb.set_pin_handlers(lambda btn: (btn in pinned) if btn is not b["sfcoa"] else None,
                        set_pinned)
    tb.fit(tb.measured_widths()[ICONS] - 1)
    tb._rebuild_menu()
    pins = {a.text(): a for a in tb._more_menu.actions() if a.text().startswith("Pin ")}
    assert set(pins) == {"Pin MESO", "Pin PREV LOCS"}
    assert pins["Pin PREV LOCS"].isChecked() and not pins["Pin MESO"].isChecked()

    pins["Pin MESO"].trigger()
    pins["Pin PREV LOCS"].trigger()
    assert calls == [("MESO", True), ("PREV LOCS", False)]
    # pin toggles don't open the drawer
    assert not b["meso"].isChecked()


def test_overflow_menu_without_pin_handlers(app):
    tb, _ = _build()
    tb.fit(tb.measured_widths()[ICONS] - 1)
    tb._rebuild_menu()
    assert not [a for a in tb._more_menu.actions() if a.text().startswith("Pin ")]


def test_toolbar_paints_its_pill(app):
    """A QWidget subclass needs WA_StyledBackground, or the pill is see-through."""
    host = QWidget()
    host.setObjectName("host")
    host.setStyleSheet(DARK_THEME + " QWidget#host { background-color: #FF0000; }")
    _hosts.append(host)
    tb = ResponsiveToolbar(host)
    tb.add_button("RADAR", "radar", "radar")
    tb.finish()
    tb.adjustSize()
    host.resize(tb.width() + 20, tb.height() + 20)
    tb.move(10, 10)
    img = host.grab().toImage()
    # inside the pill's left padding, clear of the rounded corners
    pill = img.pixelColor(10 + 4, 10 + tb.height() // 2)
    assert pill.red() < 100, pill.name()


@pytest.mark.parametrize("tier", [FULL, ICONS])
def test_button_content_is_centered(app, tier):
    """Icon and label sit in the middle of each button, not pushed left by
    the slack Qt adds after the label."""
    tb, b = _build()
    w = tb.measured_widths()
    tb.fit(10_000 if tier == FULL else w[FULL] - 1)
    tb.adjustSize()
    tb.parent().resize(tb.size())
    tb.parent().show()
    img = tb.grab().toImage()
    rows = range(6, img.height() - 6)
    for key, btn in b.items():
        if btn.isChecked():
            continue
        g = btn.geometry()
        ink = [x for x in range(g.x(), g.x() + g.width())
               if any(img.pixelColor(x, y).lightness() > 110 for y in rows)]
        left = ink[0] - g.x()
        right = g.x() + g.width() - 1 - ink[-1]
        # icon shapes leave a pixel or two of side bearing
        assert abs(left - right) <= 3, (key, left, right)


@pytest.mark.parametrize("tier", [FULL, ICONS])
def test_buttons_evenly_spaced_across_groups(app, tier):
    tb, b = _build()
    w = tb.measured_widths()
    tb.fit(10_000 if tier == FULL else w[FULL] - 1)
    tb.adjustSize()
    tb.parent().resize(tb.size())
    tb.parent().show()
    app.processEvents()
    shown = [btn for group in tb._groups for btn in group if not btn.isHidden()]
    gaps = {nxt.geometry().left() - (prev.geometry().right() + 1)
            for prev, nxt in zip(shown, shown[1:])}
    assert gaps == {tb._layout.spacing()}
    # each divider sits in the gap before its group's first button
    for sep, group in zip(tb._separators, tb._groups[1:]):
        first = group[0]
        assert first.geometry().left() - tb._layout.spacing() <= sep.x() < first.geometry().left()
