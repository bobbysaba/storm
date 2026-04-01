#!/usr/bin/env python3
# publish_test_retained.py
#
# Publishes test retained messages to verify the app can receive them

import sys
import time
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected: {reason_code}")

def main():
    print(f"Connecting to {config.MQTT_HOST}:{config.MQTT_PORT}...")
    
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"storm-test-publisher-{int(time.time())}",
        protocol=mqtt.MQTTv5,
    )
    
    client.on_connect = on_connect
    
    if config.MQTT_USE_TLS:
        import ssl
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_verify_locations(cafile=config.MQTT_CA_CERT)
        ctx.load_cert_chain(certfile=config.MQTT_CERT_FILE, keyfile=config.MQTT_KEY_FILE)
        client.tls_set_context(ctx)
    
    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    client.loop_start()
    time.sleep(1)
    
    # Publish test vehicle
    now = datetime.now(timezone.utc)
    vehicle_payload = {
        "vehicle_id": "TEST-VEHICLE-1",
        "lat": 35.2226,
        "lon": -97.4395,
        "gps_date": now.strftime("%d%m%y"),
        "gps_time": now.strftime("%H%M%S"),
        "wspd": 5.0,
        "wdir": 180.0,
        "t_fast": 25.0,
        "dewpoint": 15.0,
        "pressure": 1013.25
    }
    
    props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
    props.MessageExpiryInterval = 10800  # 3 hours
    
    result = client.publish(
        "storm/vehicles/TEST-VEHICLE-1",
        json.dumps(vehicle_payload),
        qos=1,
        retain=True,
        properties=props
    )
    result.wait_for_publish()
    print(f"Published test vehicle: {vehicle_payload}")
    
    # Publish test annotation
    annotation_payload = {
        "id": "test-annotation-1",
        "type_key": "road_closure",
        "lat": 35.2226,
        "lon": -97.4395,
        "label": "Test Road Closure",
        "created_at": now.isoformat(),
        "ttl_hours": 24
    }
    
    props2 = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
    props2.MessageExpiryInterval = 86400  # 24 hours
    
    result = client.publish(
        "storm/annotations/test-annotation-1",
        json.dumps(annotation_payload),
        qos=1,
        retain=True,
        properties=props2
    )
    result.wait_for_publish()
    print(f"Published test annotation: {annotation_payload}")
    
    # Publish test storm cone
    cone_payload = {
        "id": "test-cone-1",
        "lat": 35.2226,
        "lon": -97.4395,
        "heading": 90.0,
        "speed_kts": 30.0,
        "created_at": now.isoformat()
    }
    
    props3 = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
    props3.MessageExpiryInterval = 3600  # 1 hour
    
    result = client.publish(
        "storm/cones/test-cone-1",
        json.dumps(cone_payload),
        qos=1,
        retain=True,
        properties=props3
    )
    result.wait_for_publish()
    print(f"Published test storm cone: {cone_payload}")
    
    time.sleep(1)
    client.loop_stop()
    client.disconnect()
    
    print("\nDone! Run check_retained.py to verify they were stored.")

if __name__ == "__main__":
    main()
