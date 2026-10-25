"""Tests for the headless publisher package (headless/storm_headless)."""

import json
import os
from datetime import datetime, timezone

import pytest

from headless.storm_headless.obs_tail import GPS_KA_FIELD_MAP, ObsFileTail
from headless.storm_headless.observation import Observation
from headless.storm_headless.runner import PublishLoop
from headless.storm_headless.settings import SettingsError, load_settings
from headless.storm_headless.vehicle_payload import (
    build_payload,
    observation_from_payload,
    vehicle_topic,
)

FOFS_HEADER = "gps_date,gps_time,lat,lon,t_fast,dewpoint,sfc_wspd,sfc_wdir,pressure\n"
NOW = datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc)


def _row(sec: int, lat: float = 35.2) -> str:
    return f"060526,1800{sec:02d},{lat},-97.4,25.5,18.0,5.0,210,965.2\n"


def _tail(tmp_path, now=lambda: NOW, **kw) -> ObsFileTail:
    return ObsFileTail(str(tmp_path), "probe1", now_fn=now, **kw)


def _touch_newer(path):
    # mtime resolution can hide back-to-back writes; force a visible change
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# --- payload ---------------------------------------------------------------

def test_payload_matches_existing_wire_format():
    obs = Observation("probe1", 35.2, -97.4, NOW, temperature_c=25.5, dewpoint_c=18.0,
                      wind_speed_ms=5.0, wind_dir_deg=210.0, pressure_mb=965.2)
    assert json.dumps(build_payload(obs, default_icon="mesonet")) == json.dumps({
        "vehicle_id": "probe1", "lat": 35.2, "lon": -97.4,
        "gps_date": "060526", "gps_time": "180000", "icon_type": "mesonet",
        "wspd": 5.0, "wdir": 210.0, "t_fast": 25.5, "dewpoint": 18.0, "pressure": 965.2,
    })


def test_payload_omits_missing_met_fields():
    obs = Observation("probe1", 35.2, -97.4, NOW)
    assert set(build_payload(obs)) == {"vehicle_id", "lat", "lon", "gps_date", "gps_time"}


def test_payload_round_trip():
    obs = Observation("probe1", 35.2, -97.4, NOW, icon_type="car", temperature_c=25.5)
    back = observation_from_payload(build_payload(obs))
    assert (back.vehicle_id, back.lat, back.timestamp, back.icon_type, back.temperature_c) == \
        ("probe1", 35.2, NOW, "car", 25.5)
    assert vehicle_topic("probe1") == "storm/vehicles/probe1"


def test_gui_vehicle_sync_uses_shared_payload():
    vehicle_sync = pytest.importorskip("network.vehicle_sync")
    assert vehicle_sync._observation_from_payload is observation_from_payload


# --- file tail -------------------------------------------------------------

def test_tail_returns_latest_complete_row_only(tmp_path):
    f = tmp_path / "20260506.txt"
    f.write_text(FOFS_HEADER + _row(0) + _row(1) + "060526,180002,35.")   # partial last row
    tail = _tail(tmp_path)

    obs = tail.poll()
    assert obs.timestamp == datetime(2026, 5, 6, 18, 0, 1, tzinfo=timezone.utc)
    assert (obs.vehicle_id, obs.temperature_c, obs.pressure_mb) == ("probe1", 25.5, 965.2)
    assert tail.poll() is None   # unchanged file

    # finishing the partial row is picked up using the cached header
    with f.open("a") as fh:
        fh.write("2,-97.4,26.0,18.0,5.0,210,965.0\n")
    _touch_newer(f)
    obs = tail.poll()
    assert obs.timestamp.second == 2 and obs.temperature_c == 26.0


def test_tail_waits_for_file_and_rolls_over_at_midnight(tmp_path):
    clock = {"now": NOW}
    tail = _tail(tmp_path, now=lambda: clock["now"])
    assert tail.poll() is None   # file not created yet

    (tmp_path / "20260506.txt").write_text(FOFS_HEADER + _row(0))
    assert tail.poll() is not None

    clock["now"] = datetime(2026, 5, 7, 0, 0, 5, tzinfo=timezone.utc)
    (tmp_path / "20260507.txt").write_text(FOFS_HEADER + _row(5, lat=36.0))
    obs = tail.poll()
    assert tail.current_path.name == "20260507.txt" and obs.lat == 36.0


def test_tail_gps_ka_layout(tmp_path):
    (tmp_path / "GPS_ka1_20260506.txt").write_text(
        "ddmmyy,hhmmss[UTC],Latitude,Longitude\n060526,180010,35.3,-97.5\n")
    obs = _tail(tmp_path, field_map=GPS_KA_FIELD_MAP, gps_mode=True).poll()
    assert (obs.lat, obs.lon, obs.temperature_c) == (35.3, -97.5, None)
    assert set(build_payload(obs)) == {"vehicle_id", "lat", "lon", "gps_date", "gps_time"}


# --- publish loop ----------------------------------------------------------

class _FakeTail:
    def __init__(self):
        self.queue = []

    def poll(self):
        return self.queue.pop(0) if self.queue else None


class _FakePublisher:
    connected = True
    published_count = 0

    def __init__(self):
        self.sent = []

    def publish_latest(self, obs):
        self.sent.append(obs)


def test_publish_loop_throttles_and_sends_latest():
    tail, pub, clock = _FakeTail(), _FakePublisher(), {"t": 0.0}
    loop = PublishLoop(tail, pub, publish_s=5.0, clock=lambda: clock["t"])
    obs = [Observation("probe1", 35.0 + i, -97.0, NOW) for i in range(4)]

    tail.queue.append(obs[0]); loop.tick()                 # t=0: first obs goes out immediately
    clock["t"] = 1; tail.queue.append(obs[1]); loop.tick() # throttled
    clock["t"] = 2; tail.queue.append(obs[2]); loop.tick() # throttled, replaces obs[1]
    clock["t"] = 5; loop.tick()                            # window open: newest pending is sent
    clock["t"] = 11; loop.tick()                           # nothing new: nothing sent
    assert pub.sent == [obs[0], obs[2]]


# --- settings --------------------------------------------------------------

def test_settings_resolve_relative_to_config(tmp_path):
    cfg = tmp_path / "storm-headless.toml"
    cfg.write_text('vehicle_id = "Probe 1"\ndata_dir = "mesonet"\nicon_type = "mesonet"\n')
    s = load_settings(cfg)
    assert s.vehicle_id == "probe-1"
    assert s.effective_client_id == "probe-1-pi"
    assert s.data_dir == tmp_path / "mesonet"
    assert s.cert_file == tmp_path / "aws" / "storm.pem.crt"


def test_settings_cli_overrides_and_validation(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "/data"\n')
    assert load_settings(cfg, vehicle_id="probe2", client_id="x").effective_client_id == "x"
    with pytest.raises(SettingsError):
        load_settings(cfg, icon_type="boat")
    with pytest.raises(SettingsError):
        load_settings(None, data_dir="/data")   # missing vehicle_id


# --- cli -------------------------------------------------------------------

def test_check_reports_missing_certs(tmp_path, capsys):
    from headless.storm_headless.__main__ import main
    (tmp_path / "mesonet").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "mesonet"\n')

    assert main(["-c", str(cfg), "--check"]) == 1
    assert "not ready" in capsys.readouterr().out
    # without certs the dry-run setup is still usable
    assert main(["-c", str(cfg), "--check", "--dry-run"]) == 0
