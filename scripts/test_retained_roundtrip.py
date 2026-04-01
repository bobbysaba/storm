#!/usr/bin/env python3
"""
Publishes a retained message, disconnects, reconnects fresh, and checks delivery.
Run from project root:  python scripts/test_retained_roundtrip.py
"""
import sys, time, ssl
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config

TEST_TOPIC = "storm/test-retained-probe"
TEST_PAYLOAD = b"retained-test-ok"

def _make_client(client_id):
    c = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv5,
    )
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
    ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
    c.tls_set_context(ctx)
    return c

# ── Step 1: publish retained ──────────────────────────────────────────────────
print("Step 1: publishing retained message ...")
pub = _make_client("storm-probe-pub")
pub.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
pub.loop_start()
time.sleep(1)
info = pub.publish(TEST_TOPIC, TEST_PAYLOAD, qos=1, retain=True)
info.wait_for_publish(timeout=5)
print(f"  published (rc={info.rc})")
pub.loop_stop()
pub.disconnect()
time.sleep(1)

# ── Step 2: fresh subscribe ───────────────────────────────────────────────────
print("Step 2: fresh client subscribing ...")
received = []

def on_connect(client, userdata, flags, rc, props):
    client.subscribe(TEST_TOPIC, qos=1)

def on_message(client, userdata, msg):
    print(f"  [RX] retain={msg.retain}  payload={msg.payload!r}")
    received.append(msg)

sub = _make_client("storm-probe-sub")
sub.on_connect = on_connect
sub.on_message = on_message
sub.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
sub.loop_start()
time.sleep(5)
sub.loop_stop()
sub.disconnect()

# ── Step 3: clean up retained ─────────────────────────────────────────────────
print("Step 3: clearing retained message ...")
clr = _make_client("storm-probe-clr")
clr.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
clr.loop_start()
time.sleep(1)
clr.publish(TEST_TOPIC, b"", qos=1, retain=True)
time.sleep(1)
clr.loop_stop()
clr.disconnect()

# ── Result ────────────────────────────────────────────────────────────────────
print()
if received and received[0].retain:
    print("✓ RETAINED MESSAGES WORKING — broker stored and delivered correctly.")
elif received:
    print("⚠ Message received but retain=False — broker delivered live but did not store.")
else:
    print("✗ NO MESSAGE RECEIVED — broker is not storing retained messages.")
    print("  → Go to AWS Console → IoT Core → Settings → enable 'Retained messages'.")
