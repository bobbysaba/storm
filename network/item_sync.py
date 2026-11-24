"""Shared items (annotations, drawings, storm cones) synced over MQTT: one
retained message per item under its topic category, deletes included."""
import json
import logging
from datetime import datetime, timezone

from PyQt6.QtCore import QObject

from network import topics
from network.expiry import seconds_until_reset
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)


class ItemSync(QObject):
    """Bidirectional sync of one kind of item. Subclasses set CATEGORY (the
    topics.* category) and ITEM (a class with from_dict / to_dict / id), and
    emit their own signals from _received and _deleted."""

    CATEGORY: str
    ITEM: type

    def __init__(self, mqtt_client: MQTTClient, read_only: bool = False, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._root = mqtt_client.topic_root
        self._read_only = read_only
        self._name = type(self).__name__
        # re-subscribe every time the broker connection (re)establishes
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        sub = topics.subscription(self._root, self.CATEGORY)
        self._mqtt.subscribe(sub)
        log.info("%s: subscribed to %s", self._name, sub)

    def publish_create(self, item):
        self._publish(item.id, item.to_dict())

    def publish_update(self, item):
        self._publish(item.id, item.to_dict())

    def publish_delete(self, item_id: str):
        self._publish(item_id, {
            "id": item_id,
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })

    def _expiry(self) -> int:
        """Seconds a published item is retained: until the daily reset."""
        return seconds_until_reset(self._mqtt.daily_reset_utc)

    # Everything is retained, deletes included: a client that connects late
    # gets the current items, and one that was offline during a delete
    # still hears it.  Retained messages go when their expiry runs out.
    def _publish(self, item_id: str, payload: dict):
        if self._read_only:
            return
        topic = topics.topic(self._root, self.CATEGORY, item_id)
        try:
            self._mqtt.publish(topic, json.dumps(payload), expiry=self._expiry(), retain=True)
            log.debug("%s: published %s", self._name, topic)
        except Exception as e:
            log.warning("%s: publish failed: %s", self._name, e)

    def _on_message(self, topic: str, raw: bytes):
        topic_id = topics.item_id(self._root, self.CATEGORY, topic)
        if topic_id is None:
            return
        if not raw:
            # an empty retained payload clears the item (standard MQTT)
            raw = json.dumps({"id": topic_id, "deleted": True}).encode()
        try:
            data = json.loads(raw.decode())
        except Exception as e:
            log.warning("%s: JSON parse error: %s", self._name, e)
            return

        if data.get("deleted"):
            item_id = data.get("id", "")
            if item_id:
                log.debug("%s: remote delete %s", self._name, item_id)
                self._deleted(item_id, data.get("deleted_at", ""))
        else:
            try:
                item = self.ITEM.from_dict(data)
            except Exception as e:
                log.warning("%s: from_dict error: %s", self._name, e)
                return
            log.debug("%s: remote item %s", self._name, item.id)
            self._received(item)

    def _received(self, item):
        raise NotImplementedError

    def _deleted(self, item_id: str, deleted_at: str):
        raise NotImplementedError
