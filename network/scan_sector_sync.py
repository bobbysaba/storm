# network/scan_sector_sync.py
# Syncs active vehicle scan sectors over MQTT.
#
# Topic layout: storm/scan_sectors/{vehicle_id}
# Deactivation payload: {"vehicle_id": "...", "active": false, ...}

import json
import logging
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QObject, pyqtSignal

from core.scan_sector import ScanSector
from network.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

_TOPIC_PREFIX = "storm/scan_sectors"


def _next_8am_utc_seconds() -> int:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


class ScanSectorSync(QObject):
    """Bidirectional current scan-sector sync."""

    scan_received = pyqtSignal(object)  # ScanSector

    def __init__(self, mqtt_client: MQTTClient, read_only: bool = False, parent=None):
        super().__init__(parent)
        self._mqtt = mqtt_client
        self._read_only = read_only
        self._mqtt.connected.connect(self._on_mqtt_connected)
        self._mqtt.message_received.connect(self._on_message)

    def _on_mqtt_connected(self):
        self._mqtt.subscribe(f"{_TOPIC_PREFIX}/+")
        log.info("ScanSectorSync: subscribed to %s/+", _TOPIC_PREFIX)

    def publish(self, scan: ScanSector):
        if self._read_only:
            return
        topic = f"{_TOPIC_PREFIX}/{scan.vehicle_id}"
        try:
            self._mqtt.publish(
                topic,
                json.dumps(scan.to_dict()),
                expiry=_next_8am_utc_seconds(),
                retain=True,
            )
            log.debug("ScanSectorSync: published %s", topic)
        except Exception as e:
            log.warning("ScanSectorSync: publish failed: %s", e)

    def _on_message(self, topic: str, raw: bytes):
        if not topic.startswith(_TOPIC_PREFIX + "/"):
            return
        try:
            data = json.loads(raw.decode())
            scan = ScanSector.from_dict(data)
            log.debug("ScanSectorSync: remote scan %s active=%s", scan.vehicle_id, scan.active)
            self.scan_received.emit(scan)
        except Exception as e:
            log.warning("ScanSectorSync: payload parse failed: %s", e)
