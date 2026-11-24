"""storm.toml (settings/) and the [mqtt] schema it shares with the headless
publisher (headless/storm_headless/mqtt_settings.py)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

import settings
from headless.storm_headless.mqtt_settings import MQTTSettingsError, parse_mqtt
from network.mqtt_client import MQTTClient

ROOT = Path(__file__).resolve().parent.parent


def _load(tmp_path, text):
    path = tmp_path / "storm.toml"
    path.write_text(text)
    return settings.load(path)


# --- [mqtt] ------------------------------------------------------------------

def test_no_file_means_no_broker(tmp_path):
    s = settings.load(tmp_path / "storm.toml")
    assert not s.found and not s.error
    assert not s.mqtt.configured
    assert s.broker_problems("vehicle") == ["no broker is set: add an [mqtt] host to storm.toml (not found)"]


@pytest.mark.parametrize("mode", ["viewer", "archive"])
def test_viewer_and_archive_never_need_a_broker(tmp_path, mode):
    assert settings.load(tmp_path / "storm.toml").broker_problems(mode) == []


def test_defaults_follow_the_tls_mode():
    assert parse_mqtt({"host": "h"}, Path(".")).port == 8883
    assert parse_mqtt({"host": "h", "tls": "none"}, Path(".")).port == 1883
    assert parse_mqtt({"host": "h", "tls": "none", "port": 1884}, Path(".")).port == 1884


def test_relative_paths_resolve_against_the_project(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\ntls = "mutual"\ncert_file = "a/c.crt"\nkey_file = "/abs/k.key"\n')
    assert s.mqtt.cert_file == settings.PROJECT_DIR / "a" / "c.crt"
    assert s.mqtt.key_file == Path("/abs/k.key")


@pytest.mark.parametrize("table, message", [
    ({"tls": "maybe"}, "tls must be one of"),
    ({"tls": "mutual"}, "needs cert_file and key_file"),
    ({"port": 0}, "between 1 and 65535"),
    ({"port": "x"}, "port must be a number"),
    ({"read_only": "yes"}, "true or false"),
    ({"hots": "h"}, r"unknown key hots \(did you mean host\?\)"),
])
def test_invalid_tables_say_why(table, message):
    with pytest.raises(MQTTSettingsError, match=message):
        parse_mqtt(table, Path("."))


def test_an_invalid_file_is_reported_and_runs_without_a_broker(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\ntls = "maybe"\n')
    assert s.found and "tls must be one of" in s.error
    assert not s.mqtt.configured
    assert "storm.toml" in _load(tmp_path, "[mqtt\n").error


def test_the_password_comes_from_the_environment_first(monkeypatch):
    monkeypatch.delenv("STORM_MQTT_PASSWORD", raising=False)
    assert parse_mqtt({"password": "file"}, Path(".")).password == "file"
    monkeypatch.setenv("STORM_MQTT_PASSWORD", "env")
    assert parse_mqtt({"password": "file"}, Path(".")).password == "env"


def test_publishing_modes_report_missing_files_and_read_only(tmp_path):
    s = _load(tmp_path, f'[mqtt]\nhost = "h"\ntls = "mutual"\ncert_file = "{tmp_path}/c"\nkey_file = "{tmp_path}/k"\n')
    assert s.broker_problems("monitor") == [f"missing file: {tmp_path}/c", f"missing file: {tmp_path}/k"]
    (tmp_path / "c").write_text("x")
    (tmp_path / "k").write_text("x")
    assert s.broker_problems("vehicle") == []
    ro = _load(tmp_path, '[mqtt]\nhost = "h"\nread_only = true\n')
    assert ro.broker_problems("vehicle") == ["[mqtt] read_only is on, so this login can't publish"]


def test_server_tls_uses_the_system_store_and_none_uses_no_tls():
    assert parse_mqtt({"host": "h"}, Path(".")).tls_context() is not None
    assert parse_mqtt({"host": "h", "tls": "none"}, Path(".")).tls_context() is None


def test_tls_context_names_a_missing_file(tmp_path):
    s = parse_mqtt({"host": "h", "tls": "mutual", "cert_file": "c", "key_file": "k"}, tmp_path)
    with pytest.raises(FileNotFoundError, match="cert_file"):
        s.tls_context()


# --- [mqtt.viewer] -----------------------------------------------------------

def test_viewer_borrows_from_mqtt_and_is_always_read_only(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\nport = 1884\nusername = "team"\n'
                        '[mqtt.viewer]\nusername = "viewer"\nclient_id_prefix = "viewer-"\nread_only = false\n')
    v = s.mqtt_for(viewer=True)
    assert (v.host, v.port, v.username) == ("h", 1884, "viewer")
    assert v.read_only and v.client_id("chase1") == "viewer-chase1"
    assert s.mqtt_for(viewer=False).client_id("chase1") == "chase1"


def test_without_a_viewer_table_viewer_uses_the_mqtt_login_read_only(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\nusername = "team"\n')
    assert s.viewer_mqtt.username == "team" and s.viewer_mqtt.read_only


def test_the_viewer_password_has_its_own_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("STORM_MQTT_PASSWORD", "team-secret")
    monkeypatch.setenv("STORM_MQTT_VIEWER_PASSWORD", "viewer-secret")
    s = _load(tmp_path, '[mqtt]\nhost = "h"\nusername = "team"\n[mqtt.viewer]\nusername = "v"\n')
    assert s.mqtt.password == "team-secret" and s.viewer_mqtt.password == "viewer-secret"


# --- shipped files -----------------------------------------------------------

@pytest.mark.parametrize("name", ["storm.example.toml", "profiles/nssl.toml"])
def test_shipped_settings_files_parse(name):
    s = settings.load(ROOT / name)
    assert s.found and not s.error and s.mqtt.configured


def test_the_nssl_profile_views_with_the_shipped_viewer_certificate():
    s = settings.load(ROOT / "profiles" / "nssl.toml")
    certs = ROOT / "providers" / "nssl" / "certs"
    assert s.mqtt.tls == "mutual" and s.mqtt.cert_file == certs / "team" / "storm.pem.crt"
    assert s.providers == {"nssl"}
    v = s.viewer_mqtt
    assert v.cert_file == certs / "viewer" / "viewer.pem.crt"
    assert v.ca_cert == certs / "AmazonRootCA1.pem"
    assert v.client_id("chase1") == "viewer-chase1" and v.read_only
    assert v.missing_files() == []


# --- client ------------------------------------------------------------------

class _FakePaho:
    def __init__(self):
        self.published = []

    def publish(self, topic, *args, **kwargs):
        self.published.append(topic)


def test_a_read_only_client_never_publishes():
    client = MQTTClient(read_only=True)
    client._client = paho = _FakePaho()
    client.publish("storm/cones/abc", "{}")
    assert paho.published == []


def test_a_normal_client_publishes():
    client = MQTTClient()
    client._client = paho = _FakePaho()
    client.publish("storm/cones/abc", "{}")
    assert paho.published == ["storm/cones/abc"]


# --- [map] -------------------------------------------------------------------

def test_map_defaults_to_the_middle_of_the_us(tmp_path):
    m = settings.load(tmp_path / "storm.toml").map
    assert (m.home_lat, m.home_lon, m.zoom) == (39.5, -98.35, 4)


def test_map_home(tmp_path):
    s = _load(tmp_path, "[map]\nhome_lat = 35.22\nhome_lon = -97.44\nzoom = 6\n")
    assert not s.error
    assert (s.map.home_lat, s.map.home_lon, s.map.zoom) == (35.22, -97.44, 6)


@pytest.mark.parametrize("text, message", [
    ("home_lat = 35", "go together"),
    ("home_lat = 95\nhome_lon = 0", "home_lat must be a number from -90 to 90"),
    ('zoom = "6"', "zoom must be a number"),
    ("center = 1", "unknown key center"),
    ('time_zones = "America/Chicago"', "time_zones must be a list"),
    ('time_zones = ["America/Nowhere"]', "unknown zone 'America/Nowhere'"),
    ('time_zones = ["UTC", "UTC", "UTC", "UTC"]', "at most 3"),
])
def test_invalid_map(tmp_path, text, message):
    assert message in _load(tmp_path, f"[map]\n{text}\n").error


# --- problems: each table stands alone ------------------------------------

_BROKER = '[mqtt]\nhost = "broker.example.org"\n'


@pytest.mark.parametrize("text, table, hint", [
    ('[unit]\ntemperature = "C"\n', "[unit]", "did you mean units?"),
    ('[[source]]\nkind = "overlay"\n', "[source]", "did you mean sources?"),
    ("home_lat = 35\n", "[home_lat]", ""),
    ('[mqtt.veiwer]\nusername = "v"\n', "[mqtt.veiwer]", "did you mean viewer?"),
])
def test_an_unknown_table_is_skipped_and_the_rest_used(tmp_path, text, table, hint):
    s = _load(tmp_path, text + _BROKER)
    assert f"unknown table {table}" in s.error and hint in s.error
    assert s.found and not s.unread and s.mqtt.configured


def test_an_unknown_key_is_skipped_with_a_suggestion(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\npasword = "x"\n[units]\ntemprature = "C"\ndistance = "km"\n')
    assert s.problems == ("[mqtt]: unknown key pasword (did you mean password?) (left out)",
                          "[units]: unknown key temprature (did you mean temperature?) (left out)")
    assert s.mqtt.host == "h" and s.units.distance == "km"


def test_a_bad_table_keeps_its_defaults_and_not_the_broker(tmp_path):
    s = _load(tmp_path, _BROKER + '[units]\ntemperature = "K"\n[map]\nzoom = 99\n')
    assert s.mqtt.configured, "a bad [units] must not cost the broker"
    assert s.units.temperature == "F" and s.map.zoom == 4
    assert len(s.problems) == 2, "every problem is reported, not only the first"
    assert "[map] uses its defaults" in s.problems[0] and "[units] uses its defaults" in s.problems[1]


def test_a_bad_mqtt_table_means_no_broker_but_the_rest_loads(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "h"\nport = "x"\n[units]\ntemperature = "C"\n')
    assert not s.mqtt.configured and "no broker until it's fixed" in s.error
    assert s.units.temperature == "C"
    assert s.broker_problems("vehicle")


def test_a_bad_viewer_table_falls_back_to_the_team_login_read_only(tmp_path):
    s = _load(tmp_path, _BROKER + '[mqtt.viewer]\ntopic_root = "x"\n')
    assert "can't set topic_root" in s.error
    assert s.viewer_mqtt.host == "broker.example.org" and s.viewer_mqtt.read_only


def test_one_bad_source_is_left_out(tmp_path):
    good = '[[sources]]\nid = "ok"\nkind = "overlay"\nurl = "https://x.test/a.geojson"\n'
    bad = '[[sources]]\nid = "bad"\nkind = "overlay"\nurl = "ftp://x.test"\n'
    s = _load(tmp_path, good + bad)
    assert [src.id for src in s.sources] == ["ok"]
    assert "[[sources]] bad" in s.error and "source left out" in s.error


def test_one_bad_auth_table_is_left_out(tmp_path):
    s = _load(tmp_path, '[[auth]]\nurl = "https://a.test"\napi_key_env = "K"\n'
                        '[[auth]]\nurl = "https://b.test"\n')
    assert [a.url for a in s.auth] == ["https://a.test"] and "table left out" in s.error


def test_one_bad_logger_layout_is_left_out(tmp_path):
    s = _load(tmp_path, '[obs_columns.good]\nlat = "la"\n[obs_columns.bad]\nlat = ""\n')
    assert [name for name, _ in s.obs_layouts] == ["good"] and "layout left out" in s.error


def test_a_toml_error_means_the_file_is_unread(tmp_path):
    s = _load(tmp_path, _BROKER + "[units\n")
    assert s.unread and not s.mqtt.configured and len(s.problems) == 1


def test_the_parsers_stay_strict_without_warn():
    """The headless publisher and code providers call them without warn."""
    with pytest.raises(MQTTSettingsError, match="did you mean host"):
        parse_mqtt({"hots": "h"}, Path("."))


def test_map_time_zones(tmp_path):
    assert settings.load(tmp_path / "storm.toml").map.time_zones == ()
    s = _load(tmp_path, '[map]\ntime_zones = ["Europe/Berlin"]\n')
    assert not s.error and s.map.time_zones == ("Europe/Berlin",)


def test_the_nssl_profile_shows_central_and_mountain_time():
    zones = settings.load(ROOT / "profiles" / "nssl.toml").map.time_zones
    assert zones == ("America/Chicago", "America/Denver")


def test_the_nssl_profile_opens_on_norman():
    m = settings.load(ROOT / "profiles" / "nssl.toml").map
    assert (m.home_lat, m.home_lon, m.zoom) == (35.22, -97.44, 6)


@pytest.mark.parametrize("text, title", [
    (_BROKER + '[unit]\ntemperature = "C"\n', "Settings File Problems"),
    ("[mqtt\n", "Settings File Not Used"),
])
def test_startup_lists_every_problem(tmp_path, monkeypatch, text, title):
    import main
    from PyQt6.QtWidgets import QMessageBox
    import ui.map.html
    shown = []
    monkeypatch.setattr(settings, "SETTINGS", _load(tmp_path, text))
    monkeypatch.setattr(ui.map.html, "basemap_source", lambda: "offline")
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, t, body: shown.append((t, body)))
    main._warn_missing_files()
    assert [t for t, _ in shown] == [title]
    assert all(f"• {line}" in shown[0][1] for line in settings.SETTINGS.problems)
