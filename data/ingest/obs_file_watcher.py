import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# file tailing and row parsing are shared with the headless publisher
from headless.storm_headless.obs_tail import FieldMap, GPS_KA_FIELD_MAP, ObsFileTail

__all__ = ["FieldMap", "GPS_KA_FIELD_MAP", "ObsFileWatcher"]

log = logging.getLogger(__name__)


class ObsFileWatcher(QObject):
    """
    Watches today's *YYYYMMDD.txt inside a directory for new rows and
    emits obs_ready with the latest one on each poll.  Rolls over to the next
    day's file automatically at midnight.

    Thin Qt timer wrapper around ObsFileTail, which holds the file-format logic.
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, data_dir: str,
                 vehicle_id: str,
                 field_map: FieldMap | None = None,
                 poll_interval_s: int = 10,
                 gps_mode: bool = False,
                 parent=None):
        super().__init__(parent)
        self._tail    = ObsFileTail(data_dir, vehicle_id, field_map=field_map, gps_mode=gps_mode)
        self._poll_ms = poll_interval_s * 1000

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)


    def start(self):
        self._poll()
        self._timer.start(self._poll_ms)
        log.info("ObsFileWatcher: watching %s every %ds",
                 self._tail.data_dir, self._poll_ms // 1000)

    def stop(self):
        self._timer.stop()

    def _poll(self):
        obs = self._tail.poll()
        if obs is not None:
            self.obs_ready.emit(obs)
