#!/usr/bin/env python3
"""
Minimal retained-message diagnostic.
Run from project root with the storm env active:
    python scripts/test_retained.py

Subscribes to storm/# with a fresh client and prints every message received
for 10 seconds, showing topic, retain flag, and payload preview.
"""
import sys
import time
import ssl
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config

TIMEOUT = 10  # seconds to wait for messages

received = []

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[CONNECT] {reason_code}  protocol={client._protocol}  props={properties}")
    client.subscribe("storm/#", qos=1)
    print("[SUBSCRIBE] storm/#  (waiting for retained messages...)")

def on_message(client, userdata, message):
    preview = message.payload[:80].decode(errors="replace")
    print(f"[RX] retain={message.retain}  topic={message.topic}  payload={preview!r}")
    received.append(message)

def on_subscribe(client, userdata, mid, reason_codes, properties):
    print(f"[SUBACK] mid={mid}  reason_codes={reason_codes}")

c = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="storm-retained-test",
    protocol=mqtt.MQTTv5,
)
c.on_connect   = on_connect
c.on_message   = on_message
c.on_subscribe = on_subscribe

ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
c.tls_set_context(ctx)

print(f"Connecting to {config.MQTT_HOST}:{config.MQTT_PORT} ...")
c.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
c.loop_start()

time.sleep(TIMEOUT)
c.loop_stop()
c.disconnect()

print(f"\n--- {len(received)} message(s) received in {TIMEOUT}s ---")
if not received:
    print("NO MESSAGES — retained messages are not being delivered.")
else:
    retained = [m for m in received if m.retain]
    print(f"{len(retained)} retained, {len(received) - len(retained)} live")
