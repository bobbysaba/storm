# network/vehicle_sync.py
# Publishes vehicle surface obs to MQTT.
# Used by both Track A (obs file watcher) and Track B (GPS reader).
#
# Topic layout: storm/vehicles/{vehicle_id}
#
# Payload fields (JSON):
#   vehicle_id, lat, lon,
#   gps_date (DDMMYY), gps_time (HHMMSS),
#   wspd (m/s), wdir (deg), t_fast (°C), dewpoint (°C), pressure (mb)
#
# Fields are omitted from the payload when None (GPS-only vehicles will
# have no met fields).

import json
import logging

from PyQt6.QtCore import QObject

from core.observation import Observation
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/vehicles"


class VehicleSync(QObject):
    """
    Publishes vehicle observations to MQTT.
    Used by both Track A (obs file watcher) and Track B (GPS reader).
    """

    def __init__(self, mqtt_client: MQTTClient, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client

    def publish_obs(self, obs: Observation):
        topic = f"{_TOPIC_PREFIX}/{obs.vehicle_id}"
        try:
            payload = _build_payload(obs)
            self._mqtt.publish(topic, json.dumps(payload))
            log.debug("VehicleSync: published %s", topic)
        except Exception as e:
            log.warning("VehicleSync: publish failed: %s", e)


def _build_payload(obs: Observation) -> dict:
    """
    Build the outbound MQTT JSON payload.  Only includes met fields when
    present so GPS-only vehicles send a compact position-only message.
    """
    payload: dict = {
        "vehicle_id": obs.vehicle_id,
        "lat":        obs.lat,
        "lon":        obs.lon,
        # Re-derive gps_date / gps_time from the parsed timestamp so the
        # raw logger strings are preserved in the wire format.
        "gps_date":   obs.timestamp.strftime("%d%m%y"),   # DDMMYY
        "gps_time":   obs.timestamp.strftime("%H%M%S"),   # HHMMSS
    }

    if obs.wind_speed_ms  is not None: payload["wspd"]     = obs.wind_speed_ms
    if obs.wind_dir_deg   is not None: payload["wdir"]     = obs.wind_dir_deg
    if obs.temperature_c  is not None: payload["t_fast"]   = obs.temperature_c
    if obs.dewpoint_c     is not None: payload["dewpoint"] = obs.dewpoint_c
    if obs.pressure_mb    is not None: payload["pressure"] = obs.pressure_mb

    return payload
