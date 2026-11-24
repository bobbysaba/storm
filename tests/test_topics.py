"""The MQTT topic root (topic_root in [mqtt]) and the topic helpers shared by
the GUI sync modules and the headless publisher."""
import json
from datetime import datetime, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

import settings
from core.annotation import Annotation
from headless.storm_headless import topics
from headless.storm_headless.mqtt_settings import MQTTSettingsError, parse_mqtt
from headless.storm_headless.vehicle_payload import vehicle_topic
from network.annotation_sync import AnnotationSync
from network.mqtt_client import MQTTClient

ROOT = Path(__file__).resolve().parent.parent


# --- helpers -----------------------------------------------------------------

def test_topics_sit_under_the_root():
    assert topics.topic("storm", topics.CONES, "abc") == "storm/cones/abc"
    assert topics.subscription("teams/ou", topics.VEHICLES) == "teams/ou/vehicles/+"
    assert vehicle_topic("probe1") == "storm/vehicles/probe1"
    assert vehicle_topic("probe1", "lab") == "lab/vehicles/probe1"


@pytest.mark.parametrize("name, expected", [
    ("storm/drawings/abc", "abc"),
    ("storm/drawings/", None),
    ("storm/drawings/a/b", None),
    ("storm/cones/abc", None),
    ("other/drawings/abc", None),
])
def test_item_id_only_matches_its_own_kind_and_root(name, expected):
    assert topics.item_id("storm", topics.DRAWINGS, name) == expected


@pytest.mark.parametrize("raw, expected", [("storm", "storm"), (" /teams/ou/ ", "teams/ou")])
def test_validate_root_strips_slashes(raw, expected):
    assert topics.validate_root(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "a/+", "a/#", "$SYS", "a//b"])
def test_validate_root_rejects_bad_roots(raw):
    with pytest.raises(ValueError):
        topics.validate_root(raw)


# --- settings ----------------------------------------------------------------

def test_topic_root_defaults_to_storm():
    assert parse_mqtt({"host": "h"}, Path(".")).topic_root == "storm"
    assert parse_mqtt({"host": "h", "topic_root": "lab/storm"}, Path(".")).topic_root == "lab/storm"


def test_a_bad_topic_root_is_a_settings_error():
    with pytest.raises(MQTTSettingsError, match="wildcards"):
        parse_mqtt({"host": "h", "topic_root": "storm/#"}, Path("."))


def test_the_viewer_reads_the_same_topics(tmp_path):
    path = tmp_path / "storm.toml"
    path.write_text('[mqtt]\nhost = "h"\ntopic_root = "lab"\n[mqtt.viewer]\nusername = "v"\n')
    assert settings.load(path).viewer_mqtt.topic_root == "lab"

    path.write_text('[mqtt]\nhost = "h"\n[mqtt.viewer]\ntopic_root = "lab"\n')
    assert "can't set topic_root" in settings.load(path).error


def test_the_nssl_profile_keeps_the_storm_root():
    # the viewer certificate's AWS IoT policy only allows storm/*
    assert settings.load(ROOT / "profiles" / "nssl.toml").mqtt.topic_root == "storm"


def test_the_headless_config_reads_topic_root(tmp_path):
    from headless.storm_headless.settings import load_settings
    path = tmp_path / "storm-headless.toml"
    path.write_text('vehicle_id = "probe1"\ndata_dir = "d"\n[mqtt]\nhost = "h"\ntopic_root = "lab"\n')
    assert load_settings(path).mqtt.topic_root == "lab"


# --- sync modules ------------------------------------------------------------

class _FakePaho:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, *args, **kwargs):
        self.published.append(topic)

    def subscribe(self, topic, **kwargs):
        self.subscribed.append(topic)


def test_sync_modules_use_the_clients_root():
    client = MQTTClient(topic_root="lab")
    client._client = paho = _FakePaho()
    sync = AnnotationSync(client)
    received = []
    sync.annotation_received.connect(received.append)

    client.connected.emit()
    assert paho.subscribed == ["lab/annotations/+"]

    ann = Annotation.new("flooded", 35.0, -97.0)
    sync.publish_create(ann)
    assert paho.published == [f"lab/annotations/{ann.id}"]

    payload = json.dumps(ann.to_dict()).encode()
    client.message_received.emit(f"storm/annotations/{ann.id}", payload)
    assert received == []
    client.message_received.emit(f"lab/annotations/{ann.id}", payload)
    assert [a.id for a in received] == [ann.id]


# --- vehicle payloads from other publishers ---------------------------------

def test_generic_vehicle_field_names_are_accepted():
    from headless.storm_headless.vehicle_payload import observation_from_payload
    obs = observation_from_payload({
        "vehicle_id": "v1", "lat": 35, "lon": -97,
        "timestamp": "2026-05-06T18:00:00Z",
        "temperature_c": 25.5, "dewpoint_c": 18, "wind_speed_ms": 5,
        "wind_dir_deg": 210, "pressure_mb": 965.2,
    })
    assert obs.timestamp == datetime(2026, 5, 6, 18, tzinfo=timezone.utc)
    assert (obs.temperature_c, obs.dewpoint_c, obs.wind_speed_ms, obs.wind_dir_deg, obs.pressure_mb) == \
        (25.5, 18.0, 5.0, 210.0, 965.2)


def test_a_naive_or_bad_timestamp():
    from headless.storm_headless.vehicle_payload import observation_from_payload
    base = {"vehicle_id": "v1", "lat": 35, "lon": -97}
    naive = observation_from_payload(base | {"timestamp": "2026-05-06T18:00:00"})
    assert naive.timestamp == datetime(2026, 5, 6, 18, tzinfo=timezone.utc)
    # an unparseable timestamp falls back to gps_date + gps_time
    bad = observation_from_payload(base | {"timestamp": "soon", "gps_date": "060526", "gps_time": "180000"})
    assert bad.timestamp == datetime(2026, 5, 6, 18, tzinfo=timezone.utc)


def test_hailcam_is_a_headless_icon(tmp_path):
    from headless.storm_headless.settings import load_settings
    path = tmp_path / "storm-headless.toml"
    path.write_text('vehicle_id = "hail1"\ndata_dir = "d"\nicon_type = "hailcam"\n')
    assert load_settings(path).icon_type == "hailcam"


# --- daily reset -------------------------------------------------------------

def test_seconds_until_reset():
    from network.expiry import seconds_until_reset
    at = datetime(2026, 5, 6, 7, 0, tzinfo=timezone.utc)
    assert seconds_until_reset(8, at) == 3600
    assert seconds_until_reset(6, at) == 23 * 3600
    assert seconds_until_reset(7, at) == 24 * 3600


def test_daily_reset_setting():
    assert parse_mqtt({"host": "h"}, Path(".")).daily_reset_utc == 8
    assert parse_mqtt({"host": "h", "daily_reset_utc": 0}, Path(".")).daily_reset_utc == 0
    for bad in (24, -1, 7.5, "8", True):
        with pytest.raises(MQTTSettingsError, match="daily_reset_utc"):
            parse_mqtt({"host": "h", "daily_reset_utc": bad}, Path("."))


def test_annotations_expire_at_the_clients_reset_hour(monkeypatch):
    import network.item_sync as item_sync
    seen = []
    monkeypatch.setattr(item_sync, "seconds_until_reset", lambda h: seen.append(h) or 60)
    client = MQTTClient(daily_reset_utc=3)
    client._client = _FakePaho()
    AnnotationSync(client).publish_create(Annotation.new("flooded", 35.0, -97.0))
    assert seen == [3]
