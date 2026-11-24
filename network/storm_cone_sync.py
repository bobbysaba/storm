from PyQt6.QtCore import pyqtSignal

from core.storm_cone import StormCone
from network import topics
from network.item_sync import ItemSync


class StormConeSync(ItemSync):
    """Bidirectional storm cone sync over MQTT."""

    CATEGORY = topics.CONES
    ITEM = StormCone

    # emitted when a remote cone arrives (create or update)
    cone_received = pyqtSignal(object)   # StormCone instance

    # emitted when a remote delete arrives
    cone_deleted = pyqtSignal(str)        # cone_id

    def _expiry(self) -> int:
        return 3600   # cones expire after an hour, not at the daily reset

    def _received(self, item):
        self.cone_received.emit(item)

    def _deleted(self, item_id: str, deleted_at: str):
        self.cone_deleted.emit(item_id)
