from datetime import datetime, timedelta, timezone

import numpy as np

from core.observation import Observation
from core.vehicle_history import VehicleHistory, VehicleHistoryStore

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)


def _obs(seconds, vid="V1", temp=20.0, **kw):
    return Observation(
        vehicle_id=vid, lat=35.0 + seconds * 1e-4, lon=-97.0,
        timestamp=T0 + timedelta(seconds=seconds), temperature_c=temp, **kw,
    )


def test_round_trip_and_missing_values():
    hist = VehicleHistory("V1")
    hist.append(_obs(0, pressure_mb=950.5))
    hist.append(_obs(1, temp=None))
    first, second = list(hist)
    assert first.timestamp == T0
    assert first.pressure_mb == 950.5
    assert first.dewpoint_c is None
    assert second.temperature_c is None
    assert hist[-1].timestamp == T0 + timedelta(seconds=1)


def test_grows_sorted_and_dedupes():
    hist = VehicleHistory("V1")
    for s in range(1000):
        hist.append(_obs(s))
    assert not hist.append(_obs(500))
    hist.append(_obs(1000.5 - 600))  # out of order
    assert len(hist) == 1001
    assert np.all(np.diff(hist.times) > 0)


def test_recent_reads_only_window():
    hist = VehicleHistory("V1")
    for s in range(3600):
        hist.append(_obs(s))
    now = T0 + timedelta(seconds=3599)
    recent = hist.recent(30, now)
    assert len(recent) == 31
    assert recent[0].timestamp == now - timedelta(seconds=30)


def test_store_persists_across_restart(tmp_path):
    store = VehicleHistoryStore(tmp_path)
    for s in range(5):
        store.append(_obs(s))
    store.append(_obs(0, vid="V2"))
    store.close()

    reloaded = VehicleHistoryStore(tmp_path)
    assert len(reloaded.get("V1")) == 5
    assert len(reloaded.get("V2")) == 1
    assert reloaded.get("V1")[2].temperature_c == 20.0
    # a re-delivered obs after restart is not duplicated
    reloaded.append(_obs(4))
    assert len(reloaded.get("V1")) == 5
    reloaded.close()


def test_store_prunes_old_days(tmp_path):
    today = datetime.now(timezone.utc).date()
    keep = tmp_path / f"vehicle_history_{today - timedelta(days=1)}.sqlite"
    old = tmp_path / f"vehicle_history_{today - timedelta(days=2)}.sqlite"
    keep.touch()
    old.touch()
    VehicleHistoryStore(tmp_path).close()
    assert keep.exists()
    assert not old.exists()


def test_store_without_dir_does_not_persist(tmp_path):
    store = VehicleHistoryStore(None)
    store.append(_obs(0))
    assert "V1" in store
    assert not list(tmp_path.iterdir())


def test_dialog_arrays_match_list_path():
    from ui.dialogs.vehicle_timeseries_dialog import _build_arrays

    hist = VehicleHistory("V1")
    obs = [_obs(s, wind_speed_ms=5.0) for s in (0, 1, 2, 100, 101)]
    for o in obs:
        hist.append(o)
    fast = _build_arrays(hist)
    slow = _build_arrays(obs)
    assert list(fast[0]) == list(slow[0])
    for a, b in zip(fast[1:6], slow[1:6]):
        np.testing.assert_allclose(a, b, rtol=1e-6)
    assert set(i for i in range(len(fast[0])) if i in fast[6]) == set(slow[6])
    assert fast[6][4].timestamp == slow[6][4].timestamp
