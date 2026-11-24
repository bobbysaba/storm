"""Live radar starts on the site nearest [map] home, and the nearest-site
picks skip the Radar Operations Center's test radar (KCRI, in Norman)."""
from types import SimpleNamespace

from ui.app.main_window_map_helpers import MainWindowMapHelpersMixin
from ui.app.main_window_radar import MainWindowRadarMixin

SITES = [{"site_id": "KCRI", "lat": 35.238, "lon": -97.460},
         {"site_id": "KTLX", "lat": 35.333, "lon": -97.278},
         {"site_id": "KATX", "lat": 48.195, "lon": -122.496}]


def _host():
    host = SimpleNamespace(_radar_station_sites=SITES)
    host._nearest_radar_site = MainWindowMapHelpersMixin._nearest_radar_site.__get__(host)
    host._nearest_nexrad = MainWindowRadarMixin._nearest_nexrad.__get__(host)
    return host


def test_norman_gets_ktlx_not_the_test_radar():
    host = _host()
    assert host._nearest_radar_site(35.22, -97.44) == "KTLX"
    assert host._nearest_nexrad(35.22, -97.44) == "KTLX"
    assert host._nearest_nexrad(35.22, -97.44, archive=True) == "KTLX"


def test_another_home_gets_its_own_site():
    assert _host()._nearest_radar_site(47.6, -122.3) == "KATX"
