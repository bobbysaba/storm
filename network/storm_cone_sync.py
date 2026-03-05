# network/storm_cone_sync.py
# Syncs storm motion cones over MQTT.
#
# Topic layout:  storm/cones/{cone_id}
# Delete payload: {"id": "...", "deleted": true}

import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from core.storm_cone import StormCone
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/cones"


class StormConeSync(QObject):
    """Bidirectional storm cone sync over MQTT."""

    # emitted when a remote cone arrives (create or update)
    cone_received = pyqtSignal(object)   # StormCone instance

    # emitted when a remote delete arrives
    cone_deleted = pyqtSignal(str)        # cone_id

    def __init__(self, mqtt_client: MQTTClient, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    # ── Subscription ──────────────────────────────────────────────────────────

    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("StormConeSync: subscribed to %s/+", _TOPIC_PREFIX)

    # ── Publish (local → broker) ───────────────────────────────────────────────

    def publish_create(self, cone: StormCone):
        self._publish(cone.id, cone.to_dict())

    def publish_update(self, cone: StormCone):
        self._publish(cone.id, cone.to_dict())

    def publish_delete(self, cone_id: str):
        self._publish(cone_id, {"id": cone_id, "deleted": True})

    def _publish(self, cone_id: str, payload: dict):
        topic = f"{_TOPIC_PREFIX}/{cone_id}"
        try:
            self._mqtt.publish(topic, json.dumps(payload))
            log.debug("StormConeSync: published %s", topic)
        except Exception as e:
            log.warning("StormConeSync: publish failed: %s", e)

    # ── Receive (broker → local) ───────────────────────────────────────────────

    def _on_message(self, topic: str, raw: bytes):
        if not topic.startswith(_TOPIC_PREFIX + "/"):
            return
        try:
            data = json.loads(raw.decode())
        except Exception as e:
            log.warning("StormConeSync: JSON parse error: %s", e)
            return

        if data.get("deleted"):
            cone_id = data.get("id", "")
            if cone_id:
                log.debug("StormConeSync: remote delete %s", cone_id)
                self.cone_deleted.emit(cone_id)
        else:
            try:
                cone = StormCone.from_dict(data)
                log.debug("StormConeSync: remote cone %s", cone.id)
                self.cone_received.emit(cone)
            except Exception as e:
                log.warning("StormConeSync: from_dict error: %s", e)
