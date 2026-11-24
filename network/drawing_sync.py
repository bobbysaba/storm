from PyQt6.QtCore import pyqtSignal

from core.drawing import DrawingAnnotation
from network import topics
from network.item_sync import ItemSync


class DrawingSync(ItemSync):
    """Bidirectional drawing annotation sync over MQTT."""

    CATEGORY = topics.DRAWINGS
    ITEM = DrawingAnnotation

    drawing_received = pyqtSignal(object)   # DrawingAnnotation instance
    drawing_deleted  = pyqtSignal(str)       # drawing_id

    def _received(self, item):
        self.drawing_received.emit(item)

    def _deleted(self, item_id: str, deleted_at: str):
        self.drawing_deleted.emit(item_id)
