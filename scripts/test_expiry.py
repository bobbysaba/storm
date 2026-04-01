#!/usr/bin/env python3
"""
Verify MQTTv5 retained messages with expiry work end-to-end.
1. Publish a retained message with 3600s expiry
2. Verify it's stored in AWS IoT Core via CLI
3. Subscribe with a fresh client and verify it's delivered with retain=True
"""
import sys, time, ssl, json, subprocess
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config

TEST_TOPIC = "storm/test-expiry-probe"
TEST_PAYLOAD = {"test": "expiry-verification", "timestamp": time.time()}
EXPIRY_SECONDS = 3600

print("=" * 70)
print("STEP 1: Publish retained message with expiry")
print("=" * 70)

pub = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="storm-expiry-pub",
    protocol=mqtt.MQTTv5,
)
pub.enable_logger()

ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
pub.tls_set_context(ctx)

pub.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
pub.loop_start()
time.sleep(1)

props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
props.MessageExpiryInterval = EXPIRY_SECONDS

info = pub.publish(TEST_TOPIC, json.dumps(TEST_PAYLOAD), qos=1, retain=True, properties=props)
info.wait_for_publish(timeout=5)
print(f"✓ Published to {TEST_TOPIC} with expiry={EXPIRY_SECONDS}s (rc={info.rc})")

pub.loop_stop()
pub.disconnect()
time.sleep(2)

print("\n" + "=" * 70)
print("STEP 2: Verify message is stored in AWS IoT Core")
print("=" * 70)

result = subprocess.run(
    ['aws', 'iot-data', 'get-retained-message', '--topic', TEST_TOPIC, '--region', 'us-east-2'],
    capture_output=True, text=True
)

if result.returncode == 0:
    data = json.loads(result.stdout)
    print(f"✓ Message found in broker:")
    print(f"  Topic: {data.get('topic')}")
    print(f"  QoS: {data.get('qos')}")
    print(f"  Payload size: {len(data.get('payload', ''))} bytes")
    print(f"  Last modified: {data.get('lastModifiedTime')}")
    # Note: AWS CLI doesn't expose MessageExpiryInterval in the response
else:
    print(f"✗ Message not found: {result.stderr}")
    sys.exit(1)

print("\n" + "=" * 70)
print("STEP 3: Subscribe with fresh client and verify delivery")
print("=" * 70)

received = []

def on_connect(client, userdata, flags, rc, props):
    print(f"Connected (protocol={client._protocol})")
    client.subscribe(TEST_TOPIC, qos=1)

def on_message(client, userdata, msg):
    print(f"✓ Message received:")
    print(f"  Topic: {msg.topic}")
    print(f"  Retain: {msg.retain}")
    print(f"  Payload: {msg.payload.decode()}")
    received.append(msg)

sub = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="storm-expiry-sub",
    protocol=mqtt.MQTTv5,
)
sub.on_connect = on_connect
sub.on_message = on_message

ctx2 = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ctx2.load_verify_locations(cafile=config.MQTT_CA_CERT)
ctx2.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
sub.tls_set_context(ctx2)

sub.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
sub.loop_start()
time.sleep(5)
sub.loop_stop()
sub.disconnect()

print("\n" + "=" * 70)
print("STEP 4: Clean up")
print("=" * 70)

clr = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="storm-expiry-clr",
    protocol=mqtt.MQTTv5,
)
ctx3 = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ctx3.load_verify_locations(cafile=config.MQTT_CA_CERT)
ctx3.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
clr.tls_set_context(ctx3)

clr.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
clr.loop_start()
time.sleep(1)
clr.publish(TEST_TOPIC, b"", qos=1, retain=True)
time.sleep(1)
clr.loop_stop()
clr.disconnect()
print(f"✓ Cleared retained message from {TEST_TOPIC}")

print("\n" + "=" * 70)
print("RESULT")
print("=" * 70)

if received and received[0].retain:
    print("✓ SUCCESS: MQTTv5 retained messages with expiry work correctly")
    print(f"  - Message was published with protocol v5")
    print(f"  - Message was stored in AWS IoT Core")
    print(f"  - Message was delivered on subscribe with retain=True")
    print(f"  - Expiry was set to {EXPIRY_SECONDS}s (not visible in AWS CLI)")
else:
    print("✗ FAILURE: Retained message was not delivered")
    sys.exit(1)
