"""Archive soundings with the NSSL provider, all from NSSL's THREDDS: the
lidar truck's radiosondes and CLAMPS TROPoe retrievals, each fetched on its
own, on top of the generic observed radiosondes
(archive/fetchers/sounding_archive_fetcher.py). PERiLS CopterSonde ascents
are withheld for now (providers/nssl/archive/drawer.py)."""

import logging
import threading
from datetime import datetime

from archive.fetchers.sounding_archive_fetcher import ArchiveSoundingFetcher
from core.qt_signals import emit_alive
from core.sounding import SoundingSet
from providers.nssl.archive.clamps_sonde_archive_fetcher import TruckPosition, fetch_clamps_sonde_soundings
from providers.nssl.archive.clamps_tropoe_archive_fetcher import fetch_clamps_tropoe_soundings

log = logging.getLogger(__name__)


class NsslArchiveSoundingFetcher(ArchiveSoundingFetcher):
    """ArchiveSoundingFetcher plus NSSL / CLAMPS profiles: the lidar truck's
    radiosondes and TROPoe retrievals."""

    # how the Skew-T's comparison pills name them
    pill_labels = {"retrieval": "TROPoe"}

    # the vehicle whose track places the sondes (set_truck_position): the
    # lidar truck, which publishes as its FOFS folder name
    track_vehicle = "dltruck"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._truck_position: TruckPosition | None = None

    def set_truck_position(self, lookup: TruckPosition | None) -> None:
        """Where the lidar truck was at a time (within a minute), to place
        its sonde launches. Called from worker threads."""
        self._truck_position = lookup

    def fetch_provider_sounding(self) -> None:
        """The lidar truck's radiosondes on the clock's day, up to the clock."""
        self._start(self._do_fetch_sondes)

    def fetch_retrievals(self) -> None:
        """CLAMPS TROPoe retrievals on the clock's day, up to the clock."""
        self._start(self._do_fetch_retrievals)

    def _start(self, target) -> None:
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(target=target, args=(t,), daemon=True).start()

    def _do_fetch_sondes(self, t: datetime) -> None:
        self._fetch_one("Radiosondes", lambda: fetch_clamps_sonde_soundings(t, self._truck_position), t)

    def _do_fetch_retrievals(self, t: datetime) -> None:
        self._fetch_one("TROPoe", lambda: fetch_clamps_tropoe_soundings(t), t)

    def _fetch_one(self, name: str, fetch, t: datetime) -> None:
        """One product from THREDDS, limited to the clock's UTC day up to
        the clock: its set, or an error that says none or why not."""
        try:
            sset = _within_day(fetch(), t)
        except Exception as exc:  # noqa: BLE001
            log.warning("Archive sounding: %s failed: %s", name, exc)
            emit_alive(self, "fetch_error", f"{name} unavailable: {exc}")
            return
        if sset is None:
            emit_alive(self, "fetch_error", f"{name}: none by {t:%H:%MZ} on {t:%Y-%m-%d}")
            return
        emit_alive(self, "sounding_ready", sset)


def _within_day(sset: SoundingSet | None, t: datetime) -> SoundingSet | None:
    """The set's soundings from t's UTC midnight through t, renumbered, with
    the set placed at the latest one; None when none are left."""
    if sset is None:
        return None
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    sset.soundings = sorted((s for s in sset.soundings if day_start <= s.valid_time <= t),
                            key=lambda s: s.valid_time)
    if not sset.soundings:
        return None
    for idx, snd in enumerate(sset.soundings):
        snd.slot_offset = idx
    latest = sset.soundings[-1]
    if latest.location_source is not None or (latest.lat, latest.lon) != (0, 0):
        sset.lat, sset.lon = latest.lat, latest.lon
    if latest.height.size:
        sset.elevation = float(latest.height[0])
    sset.fetch_time = t
    return sset
