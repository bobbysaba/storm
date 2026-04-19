import json
import logging
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QObject, pyqtSignal

from core.annotation import Annotation
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/annotations"


def _next_8am_utc_seconds() -> int:
    """Seconds until the next 08:00 UTC (today if before 08z, tomorrow if at or after 08z)."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


class AnnotationSync(QObject):
    """Bidirectional annotation sync over MQTT."""

    # emitted when a remote annotation arrives (create or update)
    annotation_received = pyqtSignal(object)   # Annotation instance

    # emitted when a remote delete arrives
    annotation_deleted = pyqtSignal(str, str)   # annotation_id, deleted_at (ISO or "")

    def __init__(self, mqtt_client: MQTTClient, read_only: bool = False, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._read_only = read_only
        # re-subscribe every time the broker connection (re)establishes
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)


    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("AnnotationSync: subscribed to %s/+", _TOPIC_PREFIX)


    def publish_create(self, annotation: Annotation):
        self._publish(annotation.id, annotation.to_dict())

    def publish_update(self, annotation: Annotation):
        self._publish(annotation.id, annotation.to_dict())

    def publish_delete(self, annotation_id: str):
        self._publish(annotation_id, {
            "id": annotation_id,
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })

    def _publish(self, annotation_id: str, payload: dict):
        if self._read_only:
            return
        topic = f"{_TOPIC_PREFIX}/{annotation_id}"
        try:
            self._mqtt.publish(topic, json.dumps(payload), expiry=_next_8am_utc_seconds())
            log.debug("AnnotationSync: published %s", topic)
        except Exception as e:
            log.warning("AnnotationSync: publish failed: %s", e)


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
                deleted_at = data.get("deleted_at", "")
                log.debug("AnnotationSync: remote delete %s", ann_id)
                self.annotation_deleted.emit(ann_id, deleted_at)
        else:
            try:
                ann = Annotation.from_dict(data)
                log.debug("AnnotationSync: remote annotation %s (%s)", ann.id, ann.type_key)
                self.annotation_received.emit(ann)
            except Exception as e:
                log.warning("AnnotationSync: from_dict error: %s", e)
