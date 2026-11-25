"""network/mqtt_client.py says why a broker connection fails, and a refusal's
reason isn't overwritten by the disconnect that follows it."""
import socket
import ssl
import threading

from network import mqtt_client
from network.mqtt_client import MQTTClient, describe_connect_error


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_nothing_listening():
    port = _free_port()
    assert "nothing is listening on 127.0.0.1" in describe_connect_error("127.0.0.1", port, None)


def test_tls_to_a_port_that_doesnt_speak_it():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        conn, _ = server.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.0 400 Bad Request\r\n\r\n")   # plain text, not a TLS record
        conn.close()
    threading.Thread(target=answer, daemon=True).start()
    ctx = ssl.create_default_context()
    reason = describe_connect_error("127.0.0.1", server.getsockname()[1], ctx)
    server.close()
    assert "doesn't speak TLS" in reason or "TLS failed" in reason


class _Code:
    def __init__(self, value, failure):
        self.value, self.is_failure = value, failure

    def __str__(self):
        return "Not authorized"


def test_a_refusal_stands_over_the_disconnect_after_it():
    client = MQTTClient()
    codes = []
    client.disconnected.connect(codes.append)
    client._on_connect(None, None, None, _Code(135, True), None)
    client._on_disconnect(None, None, None, _Code(130, True), None)
    assert codes == [135]
    # a later drop is reported again
    client._on_disconnect(None, None, None, _Code(7, True), None)
    assert codes == [135, 7]


def test_a_failed_connect_is_explained(monkeypatch):
    client = MQTTClient()
    client._target = ("broker.test", 8883, None)
    monkeypatch.setattr(mqtt_client, "describe_connect_error", lambda *a: "can't find the host")
    reasons = []
    client.connect_failed.connect(reasons.append)
    client._on_connect_fail(None, None)
    assert reasons == ["can't find the host"]


def test_a_drop_before_the_broker_answers_is_a_connect_failure(monkeypatch):
    client = MQTTClient()
    client._target = ("broker.test", 8883, None)
    monkeypatch.setattr(mqtt_client, "describe_connect_error",
                        lambda *a: "the broker wants a client certificate")
    reasons, codes = [], []
    client.connect_failed.connect(reasons.append)
    client.disconnected.connect(codes.append)
    client._on_disconnect(None, None, None, _Code(128, True), None)
    assert reasons == ["the broker wants a client certificate"] and codes == []
    # once connected, a drop is a drop
    client._on_connect(None, None, None, _Code(0, False), None)
    client._on_disconnect(None, None, None, _Code(7, True), None)
    assert codes == [7]


def _pi_settings(tmp_path):
    from headless.storm_headless.settings import load_settings
    cfg = tmp_path / "pi.toml"
    cfg.write_text(f'vehicle_id = "rt-pi"\ndata_dir = "{tmp_path.as_posix()}"\n[mqtt]\nhost = "broker.test"\ntls = "none"\n')
    return load_settings(str(cfg))


def test_the_pi_logs_why_it_cant_connect_once_per_reason(tmp_path, monkeypatch, caplog):
    from headless.storm_headless import publisher
    pub = publisher.VehiclePublisher(_pi_settings(tmp_path))
    monkeypatch.setattr(publisher, "describe_connect_error", lambda *a: "can't find the host 'broker.test'")
    with caplog.at_level("WARNING"):
        pub._on_connect_fail(None, None)
        pub._on_connect_fail(None, None)
        # a drop before the broker answered is explained the same way
        pub._on_disconnect(None, None, None, _Code(128, True), None)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["MQTT: can't connect to broker.test:1883: can't find the host 'broker.test'; retrying"]


def test_a_logger_file_without_position_columns_is_reported(tmp_path, caplog):
    from datetime import datetime, timezone
    from headless.storm_headless.obs_tail import ObsFileTail
    day = datetime.now(timezone.utc)
    (tmp_path / f"{day:%Y%m%d}.csv").write_text("2026-11-22T19:00:00Z,35.2,-97.4\n2026-11-22T19:00:01Z,35.2,-97.4\n")
    tail = ObsFileTail(str(tmp_path), "rt-pi")
    with caplog.at_level("WARNING"):
        assert tail.poll() is None
        tail.poll()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "no 'lat' or 'lon' column" in warnings[0]


def test_a_refused_subscription_is_reported():
    client = MQTTClient()
    client._pending_subs[7] = "storm/annotations/+"
    refused = []
    client.subscribe_refused.connect(lambda topic, why: refused.append((topic, why)))
    client._on_subscribe(None, None, 7, [_Code(135, True)], None)
    client._on_subscribe(None, None, 8, [_Code(1, False)], None)   # granted QoS 1
    assert refused == [("storm/annotations/+", "Not authorized")]
