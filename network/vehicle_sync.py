
import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

import config
from core.observation import Observation
from network import topics
from network.mqtt_client import MQTTClient
# the wire format is shared with the headless publisher
from headless.storm_headless.vehicle_payload import (
    PUBLISH_EXPIRY_S,
    PUBLISH_QOS,
    build_payload,
    observation_from_payload as _observation_from_payload,
    vehicle_topic,
)

log = logging.getLogger(__name__)


class VehicleSync(QObject):
    """
    Bidirectional vehicle observation sync over MQTT.
    Used by both Track A (obs file watcher) and Track B (GPS reader).
    """

    vehicle_received = pyqtSignal(object)   # Observation instance

    def __init__(self, mqtt_client: MQTTClient, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._root = mqtt_client.topic_root
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        sub = topics.subscription(self._root, topics.VEHICLES)
        self._mqtt.subscribe(sub)
        log.info("VehicleSync: subscribed to %s", sub)

    def publish_obs(self, obs: Observation):
        topic = vehicle_topic(obs.vehicle_id, self._root)
        try:
            payload = build_payload(obs, default_icon=config.VEHICLE_ICON)
            self._mqtt.publish(topic, json.dumps(payload), qos=PUBLISH_QOS, expiry=PUBLISH_EXPIRY_S)
            log.debug("VehicleSync: published %s", topic)
        except Exception as e:
            log.warning("VehicleSync: publish failed: %s", e)

    def _on_message(self, topic: str, raw: bytes):
        if topics.item_id(self._root, topics.VEHICLES, topic) is None:
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
