from PyQt6.QtCore import pyqtSignal

from core.annotation import Annotation
from network import topics
from network.item_sync import ItemSync


class AnnotationSync(ItemSync):
    """Bidirectional annotation sync over MQTT."""

    CATEGORY = topics.ANNOTATIONS
    ITEM = Annotation

    # emitted when a remote annotation arrives (create or update)
    annotation_received = pyqtSignal(object)   # Annotation instance

    # emitted when a remote delete arrives
    annotation_deleted = pyqtSignal(str, str)   # annotation_id, deleted_at (ISO or "")

    def _received(self, item):
        self.annotation_received.emit(item)

    def _deleted(self, item_id: str, deleted_at: str):
        self.annotation_deleted.emit(item_id, deleted_at)
