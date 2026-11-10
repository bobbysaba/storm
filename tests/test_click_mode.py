import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication

from ui.click_mode import NONE, ClickModeController, kind_of


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeMap:
    """Records which map click modes are switched on, like MapWidget's set_*_mode calls."""

    def __init__(self):
        self.on: set[str] = set()
        self.clicks: list[tuple[str, float, float]] = []

    def set_mode(self, kind: str, active: bool) -> None:
        (self.on.add if active else self.on.discard)(kind)


KINDS = ("sounding", "measure", "route_pick", "annotation", "drawing", "storm_motion_fix", "asos_bbox")


@pytest.fixture
def setup(app):
    ctl = ClickModeController()
    fake = FakeMap()
    exits: list[str] = []

    def make_exit(kind):
        def on_exit(mode, next_mode):
            exits.append(mode)
            fake.set_mode(kind, False)
        return on_exit

    for kind in KINDS:
        ctl.register(kind, make_exit(kind),
                     lambda lat, lon, k=kind: fake.clicks.append((k, lat, lon)))

    def enter(mode, prompt=""):
        ctl.enter(mode, prompt)
        fake.set_mode(kind_of(mode), True)

    return ctl, fake, exits, enter


def test_entering_a_mode_exits_the_previous_one(setup):
    ctl, fake, exits, enter = setup
    for mode in ("sounding", "measure", "annotation:road_closure", "drawing:polygon",
                 "storm_motion_fix", "asos_bbox", "route_pick"):
        enter(mode)
        assert len(fake.on) == 1, fake.on
        assert ctl.mode == mode
    assert exits == ["sounding", "measure", "annotation:road_closure", "drawing:polygon",
                     "storm_motion_fix", "asos_bbox"]


def test_exit_callback_is_told_the_next_mode(app):
    ctl = ClickModeController()
    seen = []
    for kind in ("annotation", "drawing", "measure"):
        ctl.register(kind, lambda old, new: seen.append((old, new)))
    ctl.enter("annotation:road_closure")
    ctl.enter("annotation:flooded_road")   # another type of the same kind still exits
    ctl.enter("drawing:cold_front")
    ctl.exit()
    assert seen == [
        ("annotation:road_closure", "annotation:flooded_road"),
        ("annotation:flooded_road", "drawing:cold_front"),
        ("drawing:cold_front", NONE),
    ]


def test_click_goes_only_to_the_active_mode(setup):
    ctl, fake, _, enter = setup
    assert not ctl.click(35.0, -97.0)
    enter("measure")
    assert ctl.click(35.0, -97.0)
    enter("drawing:cold_front")
    assert ctl.click(36.0, -98.0)
    assert fake.clicks == [("measure", 35.0, -97.0), ("drawing", 36.0, -98.0)]


def test_exit_only_matches_the_given_kind(setup):
    ctl, fake, exits, enter = setup
    enter("sounding")
    assert not ctl.exit("measure")
    assert ctl.mode == "sounding"
    assert ctl.exit()
    assert ctl.mode == NONE and fake.on == set()
    assert not ctl.exit()
    assert exits == ["sounding"]


def test_exit_callback_that_reenters_exit_is_harmless(app):
    # e.g. unchecking the MEASURE button runs _on_measure_toggled(False) → exit("measure")
    ctl = ClickModeController()
    calls = []
    ctl.register("measure", lambda m, _next: (calls.append(m), ctl.exit("measure")))
    ctl.register("sounding", lambda m, _next: None)
    ctl.enter("measure")
    ctl.enter("sounding")
    assert calls == ["measure"]
    assert ctl.mode == "sounding"


def test_signals_and_prompt(setup):
    ctl, _, _, enter = setup
    modes, prompts = [], []
    ctl.mode_changed.connect(modes.append)
    ctl.prompt_changed.connect(prompts.append)
    ctl.set_prompt("ignored without a mode")
    enter("measure", "measure — click first point")
    ctl.set_prompt("measure — click second point")
    ctl.exit()
    assert modes == ["measure", NONE]
    assert prompts == ["measure — click first point", "measure — click second point", ""]


def test_unregistered_mode_raises(app):
    with pytest.raises(KeyError):
        ClickModeController().enter("teleport")
