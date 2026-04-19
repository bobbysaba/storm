import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from ui.controls.routing_controls import RoutingControls


class _FakeButton:
    def __init__(self):
        self._enabled = False

    def setEnabled(self, enabled):
        self._enabled = enabled

    def isEnabled(self):
        return self._enabled


def test_vehicle_picker_buttons_follow_known_vehicle_snapshot():
    controls = SimpleNamespace(
        _vehicles={},
        _btn_origin_vehicle=_FakeButton(),
        _btn_dest_vehicle=_FakeButton(),
    )

    assert not controls._btn_origin_vehicle.isEnabled()
    assert not controls._btn_dest_vehicle.isEnabled()

    RoutingControls.update_vehicles(controls, {
        "WX1": SimpleNamespace(lat=35.1, lon=-97.4),
        "BAD": SimpleNamespace(lat=None, lon=-97.5),
    })

    assert controls._vehicles == {"WX1": (35.1, -97.4)}
    assert controls._btn_origin_vehicle.isEnabled()
    assert controls._btn_dest_vehicle.isEnabled()

    RoutingControls.update_vehicles(controls, {})

    assert controls._vehicles == {}
    assert not controls._btn_origin_vehicle.isEnabled()
    assert not controls._btn_dest_vehicle.isEnabled()
