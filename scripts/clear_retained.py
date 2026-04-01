#!/usr/bin/env python3
"""Clear all retained messages under storm/* so they can be republished with MQTTv5."""
import sys, time, ssl, json, subprocess
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config

# Get list of retained topics from AWS CLI
result = subprocess.run(['aws', 'iot-data', 'list-retained-messages', '--region', 'us-east-2'],
                       capture_output=True, text=True)
data = json.loads(result.stdout)
topics = [item['topic'] for item in data.get('retainedTopics', [])]

print(f"Found {len(topics)} retained messages to clear:")
for t in topics:
    print(f"  {t}")

if not topics:
    print("Nothing to clear.")
    sys.exit(0)

input("\nPress Enter to clear all retained messages (Ctrl-C to cancel)...")

# Connect and clear
c = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="storm-clear-retained",
    protocol=mqtt.MQTTv5,
)
ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
c.tls_set_context(ctx)

c.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
c.loop_start()
time.sleep(1)

for topic in topics:
    c.publish(topic, b"", qos=1, retain=True)
    print(f"Cleared {topic}")

time.sleep(2)
c.loop_stop()
c.disconnect()

print(f"\n✓ Cleared {len(topics)} retained messages.")
print("Now open the app and recreate your annotations — they'll be stored as MQTTv5 retained messages.")
