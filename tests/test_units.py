"""[units] in storm.toml: display units for temperature and distance."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import settings
from core import units
from core.observation import Observation
from data.fetchers.routing_fetcher import fmt_distance
from ui.layers.station_plot_layer import _display_key


def _use(monkeypatch, tmp_path, text):
    path = tmp_path / "storm.toml"
    path.write_text(text)
    s = settings.load(path)
    monkeypatch.setattr(settings, "SETTINGS", s)
    return s


def test_defaults_are_fahrenheit_and_miles(monkeypatch, tmp_path):
    s = _use(monkeypatch, tmp_path, "")
    assert (s.units.temperature, s.units.distance) == ("F", "mi")
    assert units.temp(20.0) == 68.0 and units.temp_label() == "°F"
    assert fmt_distance(16093.44) == "10 mi"
    assert fmt_distance(30) == "98 ft"


def test_celsius_and_kilometres(monkeypatch, tmp_path):
    s = _use(monkeypatch, tmp_path, '[units]\ntemperature = "C"\ndistance = "km"\n')
    assert not s.error
    assert units.temp(20.0) == 20.0 and units.temp_label() == "°C"
    assert units.fmt_dist(4.25) == "4.2 km" and units.fmt_dist(12.6) == "13 km"
    assert fmt_distance(600) == "600 m"
    obs = Observation("v1", 35.0, -97.0, datetime(2026, 5, 6, tzinfo=timezone.utc),
                      temperature_c=21.4, dewpoint_c=15.6)
    assert _display_key(obs)[:2] == (21, 16)


def test_bad_units_are_an_error(monkeypatch, tmp_path):
    assert "temperature" in _use(monkeypatch, tmp_path, '[units]\ntemperature = "K"\n').error
    assert "unknown key" in _use(monkeypatch, tmp_path, '[units]\nwind = "kt"\n').error
