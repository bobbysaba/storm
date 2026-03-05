# network/annotation_sync.py
# Syncs annotations over MQTT.
#
# Outbound (local → broker): call publish_create/update/delete when the
#   local user places, edits, or removes an annotation.
#
# Inbound  (broker → local): annotation_received / annotation_deleted signals
#   fire when a *remote* client publishes.  Wire these in main_window to a
#   handler that updates the map WITHOUT calling back into publish, or you
#   will create a publish loop.
#
# Topic layout:  storm/annotations/{annotation_id}
# Delete payload: {"id": "...", "deleted": true}

import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from core.annotation import Annotation
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/annotations"


class AnnotationSync(QObject):
    """Bidirectional annotation sync over MQTT."""

    # emitted when a remote annotation arrives (create or update)
    annotation_received = pyqtSignal(object)   # Annotation instance

    # emitted when a remote delete arrives
    annotation_deleted = pyqtSignal(str)        # annotation_id

    def __init__(self, mqtt_client: MQTTClient, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        # re-subscribe every time the broker connection (re)establishes
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    # ── Subscription ──────────────────────────────────────────────────────────

    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("AnnotationSync: subscribed to %s/+", _TOPIC_PREFIX)

    # ── Publish (local → broker) ───────────────────────────────────────────────

    def publish_create(self, annotation: Annotation):
        self._publish(annotation.id, annotation.to_dict())

    def publish_update(self, annotation: Annotation):
        self._publish(annotation.id, annotation.to_dict())

    def publish_delete(self, annotation_id: str):
        self._publish(annotation_id, {"id": annotation_id, "deleted": True})

    def _publish(self, annotation_id: str, payload: dict):
        topic = f"{_TOPIC_PREFIX}/{annotation_id}"
        try:
            self._mqtt.publish(topic, json.dumps(payload))
            log.debug("AnnotationSync: published %s", topic)
        except Exception as e:
            log.warning("AnnotationSync: publish failed: %s", e)

    # ── Receive (broker → local) ───────────────────────────────────────────────

    def _on_message(self, topic: str, raw: bytes):
        if not topic.startswith(_TOPIC_PREFIX + "/"):
            return
        try:
            data = json.loads(raw.decode())
        except Exception as e:
            log.warning("AnnotationSync: JSON parse error: %s", e)
            return

        if data.get("deleted"):
            ann_id = data.get("id", "")
            if ann_id:
                log.debug("AnnotationSync: remote delete %s", ann_id)
                self.annotation_deleted.emit(ann_id)
        else:
            try:
                ann = Annotation.from_dict(data)
                log.debug("AnnotationSync: remote annotation %s (%s)", ann.id, ann.type_key)
                self.annotation_received.emit(ann)
            except Exception as e:
                log.warning("AnnotationSync: from_dict error: %s", e)
