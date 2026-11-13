"""VIEWER connects with the shipped read-only certificate; the publishing
modes use aws/, and a read-only client never publishes."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
from network.mqtt_client import MQTTClient


def _viewer_cert_at(monkeypatch, folder, *, present: bool):
    paths = [folder / "ca.pem", folder / "viewer.pem.crt", folder / "viewer-private.pem.key"]
    if present:
        for p in paths:
            p.write_text("x")
    monkeypatch.setattr(config, "VIEWER_MQTT_CA_CERT", str(paths[0]))
    monkeypatch.setattr(config, "VIEWER_MQTT_CERT_FILE", str(paths[1]))
    monkeypatch.setattr(config, "VIEWER_MQTT_KEY_FILE", str(paths[2]))
    return tuple(str(p) for p in paths)


def test_viewer_uses_the_shipped_certificate(tmp_path, monkeypatch):
    viewer = _viewer_cert_at(monkeypatch, tmp_path, present=True)
    assert config.viewer_cert_present()
    assert config.mqtt_cert_files(viewer=True) == viewer


def test_publishing_modes_use_aws_even_with_the_viewer_certificate(tmp_path, monkeypatch):
    _viewer_cert_at(monkeypatch, tmp_path, present=True)
    assert config.mqtt_cert_files(viewer=False) == (
        config.MQTT_CA_CERT, config.MQTT_CERT_FILE, config.MQTT_KEY_FILE)


def test_viewer_falls_back_to_aws_until_the_viewer_certificate_exists(tmp_path, monkeypatch):
    _viewer_cert_at(monkeypatch, tmp_path, present=False)
    assert not config.viewer_cert_present()
    assert config.mqtt_cert_files(viewer=True) == (
        config.MQTT_CA_CERT, config.MQTT_CERT_FILE, config.MQTT_KEY_FILE)


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
