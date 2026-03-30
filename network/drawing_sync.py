# network/drawing_sync.py
# Syncs DrawingAnnotation objects (fronts, polylines, polygons) over MQTT.
#
# Topic layout:  storm/drawings/{drawing_id}
# Delete payload: {"id": "...", "deleted": true}

import json
import logging
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QObject, pyqtSignal

from core.drawing import DrawingAnnotation
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/drawings"


def _next_8am_utc_seconds() -> int:
    """Seconds until 08:00 UTC the following calendar day."""
    now = datetime.now(timezone.utc)
    target = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return max(1, int((target - now).total_seconds()))


class DrawingSync(QObject):
    """Bidirectional drawing annotation sync over MQTT."""

    drawing_received = pyqtSignal(object)   # DrawingAnnotation instance
    drawing_deleted  = pyqtSignal(str)       # drawing_id

    def __init__(self, mqtt_client: MQTTClient, read_only: bool = False, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._read_only = read_only
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("DrawingSync: subscribed to %s/+", _TOPIC_PREFIX)

    def publish_create(self, drawing: DrawingAnnotation):
        self._publish(drawing.id, drawing.to_dict())

    def publish_update(self, drawing: DrawingAnnotation):
        self._publish(drawing.id, drawing.to_dict())

    def publish_delete(self, drawing_id: str):
        self._publish(drawing_id, {
            "id": drawing_id,
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })

    def _publish(self, drawing_id: str, payload: dict):
        if self._read_only:
            return
        topic = f"{_TOPIC_PREFIX}/{drawing_id}"
        try:
            self._mqtt.publish(topic, json.dumps(payload), expiry=_next_8am_utc_seconds())
            log.debug("DrawingSync: published %s", topic)
        except Exception as e:
            log.warning("DrawingSync: publish failed: %s", e)

    def _on_message(self, topic: str, raw: bytes):
        if not topic.startswith(_TOPIC_PREFIX + "/"):
            return
        try:
            data = json.loads(raw.decode())
        except Exception as e:
            log.warning("DrawingSync: JSON parse error: %s", e)
            return

        if data.get("deleted"):
            drawing_id = data.get("id", "")
            if drawing_id:
                log.debug("DrawingSync: remote delete %s", drawing_id)
                self.drawing_deleted.emit(drawing_id)
        else:
            try:
                d = DrawingAnnotation.from_dict(data)
                log.debug("DrawingSync: remote drawing %s (%s)", d.id, d.drawing_type)
                self.drawing_received.emit(d)
            except Exception as e:
                log.warning("DrawingSync: from_dict error: %s", e)
