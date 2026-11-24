"""NSSL's drawer in ARCHIVE (the archive_drawer hook; docs/providers.md): one
toolbar button whose drawer holds NSSL's public THREDDS data, for every
team.

    Vehicles     NSSL's recorded vehicles and scan sectors, the FOFS
                 one-second obs and the CLAMPS trailers' surface obs
    Lidar        the lidar truck's and CLAMPS trailers' Doppler lidar (scans,
                 wind profiles)
    Sounding     the lidar truck's radiosondes
    Retrievals   CLAMPS TROPoe

PERiLS CopterSonde ascents are withheld until NSSL's archive of them is
more complete (the reader, coptersonde_archive_fetcher.py, is kept).

With the NSSL profile ([providers.nssl] team = true) NSSL is the setup's own
team: its traffic comes through [archive] history as usual and Vehicles
starts on. For everyone else Vehicles starts off, and turning it on loads
NSSL's recorded traffic and one-second obs here, its vehicles tagged
"NSSL". Nothing here asks the NSSL API.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from core.qt_signals import emit_alive
from providers.nssl import paths

log = logging.getLogger(__name__)

# NSSL's recorder's files on THREDDS: storm.<kind>.<YYYYMMDD>
HISTORY_ROOT = paths.file_url(paths.FOFS_ANNOTATIONS)
HISTORY_PREFIX = "storm"


class NsslArchiveDrawer(QObject):
    """What STORM's provider-drawer mixin (ui/app/main_window_provider_drawers.py)
    builds NSSL's button and drawer from."""

    # {item: True / False / None (unknown)}, from check_availability()
    availability_ready = pyqtSignal(object)

    label = "NSSL"
    # what a guest's vehicles from here are tagged with: "NSSL probe1"
    tag = "NSSL"
    # (key, row label, tooltip), in drawer order
    items = (
        ("vehicles", "Vehicles", "NSSL's vehicles and scan sectors, their one-second obs and the CLAMPS trailers"),
        ("lidar", "Lidar", "Doppler lidar: the lidar truck and the CLAMPS trailers"),
        ("sounding", "Sounding", "The lidar truck's radiosondes"),
        ("retrievals", "Retrievals", "CLAMPS TROPoe temperature and humidity retrievals"),
    )

    def __init__(self, options: dict, archive_time: datetime, parent=None):
        super().__init__(parent)
        from archive import history
        from providers.nssl.archive.clamps_surface_playback import ClampsSurfacePlayback
        from providers.nssl.archive.clamps_wind_archive_fetcher import ArchiveClampsWindFetcher
        from providers.nssl.archive.raw_lidar_quicklook_fetcher import ArchiveRawLidarFetcher
        from providers.nssl.archive.sounding_archive_fetcher import NsslArchiveSoundingFetcher
        from providers.nssl.archive.vehicle_aliases import LEGACY_IDS
        from providers.nssl.archive.vehicle_obs_archive_fetcher import FofsVehicleObsSource
        self.team = options.get("team") is True
        self._archive_time = archive_time
        self.lidar = ArchiveRawLidarFetcher(archive_time, parent=parent)
        self.wind_profiles = ArchiveClampsWindFetcher(archive_time, parent=parent)
        self.soundings = NsslArchiveSoundingFetcher(parent=parent)
        self.surface = ClampsSurfacePlayback(parent)
        # a guest's Vehicles: NSSL's recorded traffic and one-second obs
        self.history_sources = [history.UrlSource(HISTORY_ROOT, HISTORY_PREFIX)]
        self.vehicle_obs_sources = [FofsVehicleObsSource()]
        self.vehicle_renames = dict(LEGACY_IDS)

    def check_availability(self) -> None:
        """Which items have data on the session's day, in the background."""
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
        emit_alive(self, "availability_ready", availability(self._archive_time))


def availability(when: datetime) -> dict:
    """{item: True / False / None} for *when*'s session day, from what's
    known without listing folders; None (unknown) leaves an item on. Lidar is
    left to the lidar survey."""
    from providers.nssl import indexes
    day = when.date()
    out: dict = {"lidar": None}
    try:
        fofs = indexes.FOFS.get()
        out["vehicles"] = None if fofs is None else (bool(fofs.dates() & {day}) or None)
    except Exception as exc:  # noqa: BLE001 - unknown, not absent
        log.debug("NSSL availability: FOFS index: %s", exc)
        out["vehicles"] = None
    for key, index in (("sounding", indexes.SONDES), ("retrievals", indexes.TROPOE)):
        try:
            found = index.get()
        except Exception as exc:  # noqa: BLE001
            log.debug("NSSL availability: %s index: %s", key, exc)
            found = None
        out[key] = None if found is None else day in found.dates()
    return out
