
import json
import logging
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, pyqtSignal

import config
from core.observation import Observation
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/vehicles"


class VehicleSync(QObject):
    """
    Bidirectional vehicle observation sync over MQTT.
    Used by both Track A (obs file watcher) and Track B (GPS reader).
    """

    vehicle_received = pyqtSignal(object)   # Observation instance

    def __init__(self, mqtt_client: MQTTClient, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("VehicleSync: subscribed to %s/+", _TOPIC_PREFIX)

    def publish_obs(self, obs: Observation):
        topic = f"{_TOPIC_PREFIX}/{obs.vehicle_id}"
        try:
            payload = _build_payload(obs)
            self._mqtt.publish(topic, json.dumps(payload), expiry=10800)
            log.debug("VehicleSync: published %s", topic)
        except Exception as e:
            log.warning("VehicleSync: publish failed: %s", e)

    def _on_message(self, topic: str, raw: bytes):
        if not topic.startswith(_TOPIC_PREFIX + "/"):
            return
        try:
            data = json.loads(raw.decode())
        except Exception as e:
            log.warning("VehicleSync: JSON parse error: %s", e)
            return

        try:
            obs = _observation_from_payload(data)
            log.debug("VehicleSync: remote vehicle %s", obs.vehicle_id)
            self.vehicle_received.emit(obs)
        except Exception as e:
            log.warning("VehicleSync: payload parse failed: %s", e)


def _build_payload(obs: Observation) -> dict:
    """
    Build the outbound MQTT JSON payload.  Only includes met fields when
    present so GPS-only vehicles send a compact position-only message.
    """
    payload: dict = {
        "vehicle_id": obs.vehicle_id,
        "lat":        obs.lat,
        "lon":        obs.lon,
        "gps_date":   obs.timestamp.strftime("%d%m%y"),   # DDMMYY
        "gps_time":   obs.timestamp.strftime("%H%M%S"),   # HHMMSS
    }

    icon_type = obs.icon_type or config.VEHICLE_ICON
    if icon_type:
        payload["icon_type"] = icon_type
    if obs.wind_speed_ms  is not None: payload["wspd"]     = obs.wind_speed_ms
    if obs.wind_dir_deg   is not None: payload["wdir"]     = obs.wind_dir_deg
    if obs.temperature_c  is not None: payload["t_fast"]   = obs.temperature_c
    if obs.dewpoint_c     is not None: payload["dewpoint"] = obs.dewpoint_c
    if obs.pressure_mb    is not None: payload["pressure"] = obs.pressure_mb

    return payload


def _observation_from_payload(payload: dict) -> Observation:
    return Observation(
        vehicle_id=payload["vehicle_id"],
        lat=float(payload["lat"]),
        lon=float(payload["lon"]),
        timestamp=_parse_timestamp(payload.get("gps_date"), payload.get("gps_time")),
        icon_type=(payload.get("icon_type") or "").strip() or None,
        wind_speed_ms=_float_or_none(payload.get("wspd")),
        wind_dir_deg=_float_or_none(payload.get("wdir")),
        temperature_c=_float_or_none(payload.get("t_fast")),
        dewpoint_c=_float_or_none(payload.get("dewpoint")),
        pressure_mb=_float_or_none(payload.get("pressure")),
    )


def _parse_timestamp(gps_date: str | None, gps_time: str | None) -> datetime:
    date_s = (gps_date or "").strip()
    time_s = (gps_time or "").strip()
    if date_s and time_s:
        try:
            return datetime.strptime(date_s + time_s, "%d%m%y%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
