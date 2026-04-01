#!/usr/bin/env python3
# check_retained.py
#
# Connects to the MQTT broker and lists all retained messages under storm/#
#
# Usage:
#   python scripts/check_retained.py

import sys
import time
from pathlib import Path

# Add parent directory to path so we can import config
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import paho.mqtt.client as mqtt

retained_messages = []

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected: {reason_code}")
    print("Subscribing to storm/#...")
    client.subscribe("storm/#", qos=1)

def on_subscribe(client, userdata, mid, reason_code_list, properties):
    print(f"Subscribed: mid={mid} reason_codes={reason_code_list}")

def on_message(client, userdata, message):
    if message.retain:
        retained_messages.append({
            "topic": message.topic,
            "payload_len": len(message.payload),
            "payload": message.payload.decode('utf-8', errors='replace')[:200]
        })
        print(f"RETAINED: {message.topic} ({len(message.payload)} bytes)")
    else:
        print(f"LIVE: {message.topic} ({len(message.payload)} bytes)")

def main():
    print(f"Connecting to {config.MQTT_HOST}:{config.MQTT_PORT}...")
    
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"storm-retained-checker-{int(time.time())}",
        protocol=mqtt.MQTTv5,
    )
    
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    
    if config.MQTT_USE_TLS:
        import ssl
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
        ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
        client.tls_set_context(ctx)
    
    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    client.loop_start()
    
    print("Waiting 5 seconds for retained messages...")
    time.sleep(5)
    
    client.loop_stop()
    client.disconnect()
    
    print(f"\n{'='*60}")
    print(f"Found {len(retained_messages)} retained messages:")
    print(f"{'='*60}")
    
    for msg in retained_messages:
        print(f"\nTopic: {msg['topic']}")
        print(f"Length: {msg['payload_len']} bytes")
        print(f"Preview: {msg['payload'][:100]}...")

if __name__ == "__main__":
    main()
