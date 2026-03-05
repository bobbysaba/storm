#!/usr/bin/env python3
# test_mqtt_send.py
#
# Sends one (or more) random rows from veh_data/test.txt to the MQTT broker
# using the same payload format as the live obs pipeline.
#
# Usage:
#   python test_mqtt_send.py              # send one message and exit
#   python test_mqtt_send.py --loop 10   # send every 10 seconds until Ctrl-C
#   python test_mqtt_send.py --count 5   # send 5 messages then exit
#
# Reads broker settings from config.toml (same as the main app).
# Run from the project root with the storm environment active.

import argparse
import csv
import json
import random
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent
TEST_FILE  = ROOT / "veh_data" / "test.txt"
CONFIG_FILE = ROOT / "config.toml"

# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[WARN] config.toml not found — broker settings will be empty.")
        return {}
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)

# ── Test data ─────────────────────────────────────────────────────────────────

def _load_rows() -> list[dict]:
    """Load all data rows from test.txt as a list of dicts."""
    if not TEST_FILE.exists():
        print(f"[ERROR] Test file not found: {TEST_FILE}")
        sys.exit(1)
    with TEST_FILE.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("[ERROR] Test file has no data rows.")
        sys.exit(1)
    return rows

def _pick_row(rows: list[dict]) -> dict:
    return random.choice(rows)

# ── Payload ───────────────────────────────────────────────────────────────────

def _float_or_none(val: str | None) -> float | None:
    try:
        return float(val) if val and val.strip() else None
    except ValueError:
        return None

def _build_payload(row: dict, vehicle_id: str) -> dict:
    """Build the MQTT payload from a raw CSV row dict."""
    payload: dict = {
        "vehicle_id": vehicle_id,
        "lat":        _float_or_none(row.get("lat")),
        "lon":        _float_or_none(row.get("lon")),
        "gps_date":   (row.get("gps_date") or "").strip(),   # DDMMYY
        "gps_time":   (row.get("gps_time") or "").strip(),   # HHMMSS
    }

    met = {
        "wspd":     _float_or_none(row.get("sfc_wspd")),
        "wdir":     _float_or_none(row.get("sfc_wdir")),
        "t_fast":   _float_or_none(row.get("t_fast")),
        "dewpoint": _float_or_none(row.get("dewpoint")),
        "pressure": _float_or_none(row.get("pressure")),
    }
    for k, v in met.items():
        if v is not None:
            payload[k] = v

    return payload

# ── MQTT ──────────────────────────────────────────────────────────────────────

def _make_client(cfg: dict) -> mqtt.Client:
    client_id = f"storm-test-{int(time.time())}"
    c = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
    )

    use_tls = bool(cfg.get("use_tls", False))
    if use_tls:
        ca   = cfg.get("ca_cert", "") or None
        cert = cfg.get("cert_file", "") or None
        key  = cfg.get("key_file", "") or None
        c.tls_set(ca_certs=ca, certfile=cert, keyfile=key)

    return c


def _send(c: mqtt.Client, topic: str, payload: dict):
    result = c.publish(topic, json.dumps(payload), qos=1)
    result.wait_for_publish(timeout=5)
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC]  {topic}")
    print(f"  {json.dumps(payload, indent=2)}\n")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Send test obs to MQTT broker.")
    parser.add_argument("--loop",  type=float, metavar="SECONDS",
                        help="Repeat every N seconds until Ctrl-C")
    parser.add_argument("--count", type=int, default=1,
                        help="Number of messages to send (default: 1, ignored when --loop is set)")
    args = parser.parse_args()

    cfg        = _load_config()
    host       = cfg.get("host", "")
    port       = int(cfg.get("port", 8883))
    vehicle_id = cfg.get("vehicle_id", "WX1")
    topic      = f"storm/vehicles/{vehicle_id}"

    if not host:
        print("[ERROR] 'host' not set in config.toml — nowhere to publish.")
        sys.exit(1)

    rows = _load_rows()
    print(f"Loaded {len(rows)} rows from {TEST_FILE.name}")
    print(f"Connecting to {host}:{port} as {vehicle_id!r} …\n")

    c = _make_client(cfg)
    c.connect(host, port, keepalive=30)
    c.loop_start()
    time.sleep(1)   # allow handshake

    try:
        if args.loop:
            print(f"Looping every {args.loop}s — press Ctrl-C to stop.\n")
            while True:
                payload = _build_payload(_pick_row(rows), vehicle_id)
                _send(c, topic, payload)
                time.sleep(args.loop)
        else:
            for _ in range(args.count):
                payload = _build_payload(_pick_row(rows), vehicle_id)
                _send(c, topic, payload)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        c.loop_stop()
        c.disconnect()


if __name__ == "__main__":
    main()
