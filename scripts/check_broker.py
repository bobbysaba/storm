"""Check that your MQTT broker handles what STORM needs from it.

Reads the broker from storm.toml (or the file you name), connects with the
[mqtt] login and, when there is one, the read-only [mqtt.viewer] login, and
checks:

- the [mqtt] login can connect, and publish retained messages at QoS 1;
- a wildcard subscription (<root>/<kind>/+, what STORM uses) gets them, so
  late joiners see the day's annotations, drawings and cones;
- an exact-topic subscription gets them (how STORM fetches scan sectors on
  brokers that skip the wildcard case, like AWS IoT Core);
- the [mqtt.viewer] login gets them too, and the broker won't let it publish;
- MQTT v5 message expiry drops a retained message when it runs out;
- an empty retained payload clears one.

It publishes two retained deletes under <root>/annotations/check-<random>
(STORM clients ignore a delete for an item they don't have) and clears them
at the end. It takes about a minute.

    python scripts/check_broker.py                     # storm.toml (or STORM_CONFIG)
    python scripts/check_broker.py profiles/nssl.toml

Exit status: 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import tomllib
from pathlib import Path

import paho.mqtt.client as mqtt

STORM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STORM_DIR))

from headless.storm_headless import topics  # noqa: E402
import settings  # noqa: E402  - puts .env's passwords in the environment, as STORM does

EXPIRY_S = 20


def _connect(s, client_id):
    c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=s.client_id(client_id), protocol=mqtt.MQTTv5)
    ctx = s.tls_context()
    if ctx is not None:
        c.tls_set_context(ctx)
    if s.username:
        c.username_pw_set(s.username, s.password or None)
    c.storm_dropped = []
    c.on_disconnect = lambda cl, u, f, rc, p: c.storm_dropped.append(str(rc))
    up, result = threading.Event(), []

    def on_connect(cl, u, f, rc, p):
        result.append(rc)
        # MQTT v5 brokers below QoS 2 say so in the CONNACK
        c.storm_max_qos = getattr(p, "MaximumQoS", 2) if p is not None else 2
        up.set()
    c.on_connect = on_connect
    c.connect(s.host, s.port, keepalive=30)
    c.loop_start()
    if not up.wait(10) or result[0].is_failure:
        c.loop_stop()
        raise ConnectionError(result[0] if result else "no answer from the broker")
    return c


def _connect_hint(e: Exception) -> str:
    """What a failed connect means, and what to change."""
    text = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        if "key usage" in text or "basic constraints" in text.lower():
            return (f"{text}\n      The CA certificate isn't marked as a CA (basicConstraints, keyUsage); "
                    "Python 3.13 and later insist on it. See docs/broker.md, Other setups.")
        return (f"{text}\n      The broker's certificate isn't trusted, or isn't for this host name: "
                "set ca_cert in [mqtt] to your CA, and host to the name on the certificate.")
    if isinstance(e, ConnectionRefusedError):
        return f"{text}\n      Nothing listens there: check host, port and that the broker is running."
    return text


def _publish(c, topic, payload, expiry):
    props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
    props.MessageExpiryInterval = expiry
    info = c.publish(topic, payload, qos=1, retain=True, properties=props)
    info.wait_for_publish(10)
    # some brokers (AWS IoT) drop the connection instead of refusing a publish
    time.sleep(1)
    return info.is_published() and not c.storm_dropped


def _viewer_publishes(viewer, team, tag) -> bool:
    """Whether the broker keeps a retained message the viewer login sends
    (cleared again with the team login if it does)."""
    probe = topics.topic(viewer.topic_root, topics.ANNOTATIONS, f"{tag}-viewer-probe")
    c = _connect(viewer, f"{tag}-viewer-pub")
    payload = json.dumps({"id": probe.rsplit("/", 1)[1], "deleted": True}).encode()
    try:
        c.publish(probe, payload, qos=1, retain=True).wait_for_publish(5)
    except (RuntimeError, ValueError):
        pass   # refused, or the broker dropped the connection: what we want
    time.sleep(1)
    c.loop_stop()
    c.disconnect()
    kept = probe in _retained(viewer, f"{tag}-probe-read", probe, tag, seconds=3)
    if kept:
        _publish(team, probe, b"", 60)
    return kept


def _retained(s, client_id, topic_filter, tag, seconds=5):
    """Topics under the check's tag that arrive retained on a fresh subscribe."""
    got = set()
    c = _connect(s, client_id)
    c.on_message = lambda cl, u, m: got.add(m.topic) if m.retain and tag in m.topic else None
    c.subscribe(topic_filter, qos=1)
    time.sleep(seconds)
    c.loop_stop()
    c.disconnect()
    return got


def main(argv: list[str]) -> int:
    cfg = Path(argv[1]) if len(argv) > 1 else settings.config_path()
    # the logins exactly as STORM reads them (passwords from .env included)
    loaded = settings.load(cfg)
    if loaded.error:
        print(loaded.error)
        return 1
    broker = loaded.mqtt
    if not broker.configured:
        print(f"{cfg}: no [mqtt] host")
        return 1
    viewer = loaded.viewer_mqtt if "viewer" in tomllib.loads(cfg.read_text()).get("mqtt", {}) else None

    root = broker.topic_root
    tag = f"check-{secrets.token_hex(3)}"
    keep = topics.topic(root, topics.ANNOTATIONS, f"{tag}-keep")
    short = topics.topic(root, topics.ANNOTATIONS, f"{tag}-short")
    wildcard = topics.subscription(root, topics.ANNOTATIONS)
    failed = 0

    def check(label, ok, hint=""):
        nonlocal failed
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {hint}" if hint and not ok else ""))
        return ok

    print(f"{broker.host}:{broker.port}, topic root {root!r}, test topics {wildcard[:-1]}{tag}-*")
    try:
        pub = _connect(broker, f"{tag}-pub")
    except (OSError, ConnectionError) as e:   # ssl errors are OSErrors
        check("[mqtt] login connects", False, _connect_hint(e))
        return 1
    check("[mqtt] login connects", True)
    if not check("takes QoS 1", pub.storm_max_qos >= 1,
                 f"The broker allows QoS {pub.storm_max_qos} at most; STORM publishes with QoS 1. "
                 "Use a broker that supports it (Mosquitto, EMQX, HiveMQ, AWS IoT Core)."):
        return 1

    def tombstone(topic):
        return json.dumps({"id": topic.rsplit("/", 1)[1], "deleted": True}).encode()
    if not check("publishes retained", _publish(pub, keep, tombstone(keep), 600),
                 "The login may not be allowed to publish retained (AWS IoT: iot:RetainPublish)"
                 + (f"; the broker dropped the connection: {', '.join(pub.storm_dropped)}."
                    if pub.storm_dropped else ".")):
        return 1
    _publish(pub, short, tombstone(short), EXPIRY_S)
    sent = time.time()

    check("wildcard subscription gets retained messages",
          keep in _retained(broker, f"{tag}-wild", wildcard, tag),
          "Late joiners won't see the day's annotations, drawings and cones from the broker "
          "(AWS IoT Core behaves like this).")
    exact_ok = check("exact-topic subscription gets retained messages",
                     keep in _retained(broker, f"{tag}-exact", keep, tag),
                     "Late joiners won't see the current scan sectors either.")
    if viewer is not None:
        try:
            got = _retained(viewer, f"{tag}-viewer", keep, tag)
            check("[mqtt.viewer] login gets retained messages", keep in got)
            check("[mqtt.viewer] login can't publish", not _viewer_publishes(viewer, pub, tag),
                  "The broker let the viewer login publish: anyone with it can add or delete the "
                  "team's annotations. Make it read-only in the broker's access list.")
        except (OSError, ConnectionError) as e:
            check("[mqtt.viewer] login connects", False, _connect_hint(e))

    if exact_ok:
        wait = EXPIRY_S + 5 - (time.time() - sent)
        if wait > 0:
            print(f"      waiting {wait:.0f} s for a {EXPIRY_S} s expiry")
            time.sleep(wait)
        check("message expiry drops retained messages",
              not _retained(broker, f"{tag}-expired", short, tag),
              "Shared items will stay on the broker past the daily reset.")

    for t in (keep, short):
        _publish(pub, t, b"", 60)
    check("empty retained payload clears them",
          not (_retained(broker, f"{tag}-cleared", keep, tag)
               | _retained(broker, f"{tag}-cleared2", wildcard, tag)))
    pub.loop_stop()
    pub.disconnect()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
