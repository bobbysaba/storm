
import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from core.scan_sector import ScanSector
from network import topics
from network.expiry import seconds_until_reset
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)


class ScanSectorSync(QObject):
    """Bidirectional current scan-sector sync.

    Besides the wildcard subscription, it subscribes to each vehicle's own
    scan-sector topic the first time that vehicle is heard from.  AWS IoT
    Core only sends retained messages on exact-topic subscriptions, so this
    is how a late joiner there gets the current sectors; on other brokers it
    just repeats a sector it already has.
    """

    scan_received = pyqtSignal(object)  # ScanSector

    def __init__(self, mqtt_client: MQTTClient, read_only: bool = False, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._root = mqtt_client.topic_root
        self._read_only = read_only
        self._vehicle_ids: set[str] = set()   # exact-topic subscriptions
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        sub = topics.subscription(self._root, topics.SCAN_SECTORS)
        self._mqtt.subscribe(sub)
        log.info("ScanSectorSync: subscribed to %s", sub)
        # subscriptions don't survive a reconnect
        for vehicle_id in self._vehicle_ids:
            self._mqtt.subscribe(topics.topic(self._root, topics.SCAN_SECTORS, vehicle_id))

    def _watch_vehicle(self, vehicle_id: str):
        if vehicle_id in self._vehicle_ids:
            return
        self._vehicle_ids.add(vehicle_id)
        self._mqtt.subscribe(topics.topic(self._root, topics.SCAN_SECTORS, vehicle_id))

    def publish(self, scan: ScanSector):
        if self._read_only:
            return
        topic = topics.topic(self._root, topics.SCAN_SECTORS, scan.vehicle_id)
        try:
            self._mqtt.publish(
                topic,
                json.dumps(scan.to_dict()),
                expiry=seconds_until_reset(self._mqtt.daily_reset_utc),
                retain=True,
            )
            log.debug("ScanSectorSync: published %s", topic)
        except Exception as e:
            log.warning("ScanSectorSync: publish failed: %s", e)

    def _on_message(self, topic: str, raw: bytes):
        vehicle_id = topics.item_id(self._root, topics.VEHICLES, topic)
        if vehicle_id is not None:
            self._watch_vehicle(vehicle_id)
            return
        vehicle_id = topics.item_id(self._root, topics.SCAN_SECTORS, topic)
        if vehicle_id is None:
            return
        if not raw:
            # an empty retained payload clears the sector (standard MQTT)
            raw = json.dumps({"vehicle_id": vehicle_id, "active": False}).encode()
        try:
            data = json.loads(raw.decode())
            scan = ScanSector.from_dict(data)
            log.debug("ScanSectorSync: remote scan %s active=%s", scan.vehicle_id, scan.active)
            self.scan_received.emit(scan)
        except Exception as e:
            log.warning("ScanSectorSync: payload parse failed: %s", e)
