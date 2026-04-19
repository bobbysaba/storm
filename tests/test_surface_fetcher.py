from datetime import datetime, timezone
import sys
import types

if "PyQt6" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _Signal:
        def connect(self, *_args, **_kwargs):
            pass

        def emit(self, *_args, **_kwargs):
            pass

    class _QObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class _QTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _Signal()

        def setInterval(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    qtcore.QObject = _QObject
    qtcore.QTimer = _QTimer
    qtcore.pyqtSignal = lambda *_args, **_kwargs: _Signal()
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore

from data.fetchers import surface_fetcher as sf
from data.fetchers.surface_fetcher import SurfaceFetcher


def test_asos_station_cache_uses_bundled_data_file():
    assert sf._ASOS_STATIONS_FILE.name == "asos_stations.json"
    assert sf._ASOS_STATIONS_FILE.parent.name == "data"


def test_fetch_iem_batch_uses_utc_valid_timestamp():
    fetcher = SurfaceFetcher()
    fetcher._http_get = lambda _url: b"""{
      "data": [{
        "station": "OKC",
        "name": "OKLAHOMA CITY",
        "utc_valid": "2026-04-18T23:52:00Z",
        "tmpf": "68",
        "dwpf": "50",
        "sknt": "10",
        "drct": "180",
        "mslp": "1012.3",
        "lat": "35.3889",
        "lon": "-97.6006"
      }]
    }"""

    rows = fetcher._fetch_iem_batch(
        ["OKC"],
        {"OKC": {"lat": 35.3889, "lon": -97.6006, "name": "OKLAHOMA CITY"}},
    )

    assert len(rows) == 1
    obs = rows[0]["obs"]
    assert rows[0]["id"] == "surface:asos:OKC"
    assert obs.timestamp == datetime(2026, 4, 18, 23, 52, tzinfo=timezone.utc)
