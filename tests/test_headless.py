"""Tests for the headless publisher package (headless/storm_headless)."""

import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from headless.storm_headless.obs_columns import parse_obs_layouts
from headless.storm_headless.obs_tail import ObsFileTail
from headless.storm_headless.observation import Observation
from headless.storm_headless.runner import PublishLoop
from headless.storm_headless.settings import SettingsError, load_settings
from headless.storm_headless.vehicle_payload import (
    build_payload,
    observation_from_payload,
    vehicle_topic,
)

# NSSL's logger layouts, from its profile
_NSSL = Path(__file__).resolve().parent.parent / "profiles" / "nssl.toml"
LAYOUTS = dict(parse_obs_layouts(tomllib.loads(_NSSL.read_text())["obs_columns"]))
FOFS, GPS_KA = LAYOUTS["fofs"], LAYOUTS["gps_ka"]

FOFS_HEADER = "gps_date,gps_time,lat,lon,t_fast,dewpoint,sfc_wspd,sfc_wdir,pressure\n"
NOW = datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc)


def _row(sec: int, lat: float = 35.2) -> str:
    return f"060526,1800{sec:02d},{lat},-97.4,25.5,18.0,5.0,210,965.2\n"


def _tail(tmp_path, now=lambda: NOW, field_map=FOFS) -> ObsFileTail:
    return ObsFileTail(str(tmp_path), "probe1", field_map=field_map, now_fn=now)


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
    obs = _tail(tmp_path, field_map=GPS_KA).poll()
    assert (obs.lat, obs.lon, obs.temperature_c) == (35.3, -97.5, None)
    assert set(build_payload(obs)) == {"vehicle_id", "lat", "lon", "gps_date", "gps_time"}


def test_tail_daily_pattern_prefers_the_shortest_name(tmp_path):
    (tmp_path / "GPS_ka1_20260506.txt").write_text("x\n")
    (tmp_path / "20260506.txt").write_text(FOFS_HEADER + _row(0))
    assert _tail(tmp_path).poll().lat == 35.2
    # a GPS Ka logger that kept writing yesterday's file past midnight
    tail = _tail(tmp_path, now=lambda: datetime(2026, 5, 7, 0, 5, tzinfo=timezone.utc), field_map=GPS_KA)
    tail.poll()
    assert tail.current_path.name == "GPS_ka1_20260506.txt"


def test_tail_generic_layout(tmp_path):
    from headless.storm_headless.obs_columns import FieldMap
    (tmp_path / "wx1_20260506.csv").write_text(
        "time,lat,lon,temperature_c,wind_speed_ms\n2026-05-06T18:00:03Z,35.1,-97.3,21.0,4.0\n")
    obs = _tail(tmp_path, field_map=FieldMap()).poll()
    assert (obs.timestamp.second, obs.lat, obs.temperature_c, obs.wind_speed_ms) == (3, 35.1, 21.0, 4.0)


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
    assert not s.mqtt.configured      # no built-in broker


def test_settings_read_the_shared_mqtt_table(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "/data"\n'
                   '[mqtt]\nhost = "h"\ntls = "mutual"\ncert_file = "certs/c.crt"\nkey_file = "certs/k.key"\n'
                   'client_id_prefix = "team-"\n')
    s = load_settings(cfg)
    assert s.mqtt.host == "h" and s.mqtt.cert_file == tmp_path / "certs" / "c.crt"
    assert s.effective_client_id == "team-probe1-pi"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "/data"\n[mqtt]\ntls = "sometimes"\n')
    with pytest.raises(SettingsError, match="tls must be one of"):
        load_settings(cfg)


def test_settings_pick_a_named_layout(tmp_path):
    cfg = tmp_path / "h.toml"
    base = 'vehicle_id = "ka1"\ndata_dir = "."\n'
    cfg.write_text(base + '[obs_columns.truck]\ntemperature_c = "t"\n'
                          '[obs_columns.gps]\nfile = "*_????????.txt"\ntemperature_c = ""\n')
    assert load_settings(cfg).obs_columns.temperature_c == "t"
    assert load_settings(cfg).layout_name == "truck"
    assert load_settings(cfg, layout="gps").obs_columns.file == "*_????????.txt"
    with pytest.raises(SettingsError, match="layout must be one of the \\[obs_columns\\] layouts: truck, gps"):
        load_settings(cfg, layout="ka")
    cfg.write_text(base + "gps_mode = true\n")
    with pytest.raises(SettingsError, match="gps_mode is gone"):
        load_settings(cfg)


def test_settings_cli_overrides_and_validation(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "/data"\n')
    assert load_settings(cfg, vehicle_id="probe2", client_id="x").effective_client_id == "x"
    with pytest.raises(SettingsError):
        load_settings(cfg, icon_type="boat")
    with pytest.raises(SettingsError):
        load_settings(None, data_dir="/data")   # missing vehicle_id


# --- cli -------------------------------------------------------------------

def test_check_reports_a_missing_broker(tmp_path, capsys):
    from headless.storm_headless.__main__ import main
    (tmp_path / "mesonet").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "mesonet"\n')

    assert main(["-c", str(cfg), "--check"]) == 1
    out = capsys.readouterr().out
    assert "no host in the [mqtt] table" in out and "not ready" in out
    # without a broker the dry-run setup is still usable
    assert main(["-c", str(cfg), "--check", "--dry-run"]) == 0


def test_check_reports_missing_certs(tmp_path, capsys):
    from headless.storm_headless.__main__ import main
    (tmp_path / "mesonet").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text('vehicle_id = "probe1"\ndata_dir = "mesonet"\n'
                   '[mqtt]\nhost = "h"\ntls = "mutual"\ncert_file = "c.crt"\nkey_file = "k.key"\n')

    assert main(["-c", str(cfg), "--check"]) == 1
    assert "cert_file missing" in capsys.readouterr().out
