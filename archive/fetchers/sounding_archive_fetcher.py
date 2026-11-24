
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet
from core.qt_signals import emit_alive
from data.fetchers.obs_sounding_fetcher import (
    _fetch_profiles_for_timestamp,
    _format_label,
    _parse_profile,
)

log = logging.getLogger(__name__)


class ArchiveSoundingFetcher(QObject):
    """
    On-demand sounding fetcher for archive mode: observed radiosondes (IEM
    RAOB) at the archive clock rather than "now". There is no archive model
    sounding: Open-Meteo's historical HRRR came back empty or timed out
    (2026-11-11). The NSSL provider adds its own sources
    (providers/nssl/archive/sounding_archive_fetcher.py).

    Signals
    -------
    sounding_ready(SoundingSet)
    fetch_error(str)
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_archive_time: Optional[datetime] = None


    def on_time_changed(self, archive_time: datetime) -> None:
        self._current_archive_time = archive_time

    def fetch_obs_sounding(self, station_id: str, lat: float, lon: float, elev: float) -> None:
        """Fetch observed radiosonde sounding from IEM RAOB archive."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_obs,
            args=(station_id, lat, lon, elev, t),
            daemon=True,
        ).start()


    def _do_fetch_obs(
        self, station_id: str, lat: float, lon: float, elev: float, t: datetime
    ) -> None:
        try:
            raw_profiles = []
            for ts in _archive_timestamps_to_query(t):
                try:
                    raw_profiles.extend(_fetch_profiles_for_timestamp(station_id, ts))
                except Exception as exc:
                    log.warning(
                        "ArchiveSoundingFetcher: failed RAOB fetch for %s at %s: %s",
                        station_id, ts, exc,
                    )

            if not raw_profiles:
                emit_alive(self, "fetch_error", f"No observed sounding data returned for {station_id}")
                return

            soundings = []
            for idx, profile in enumerate(raw_profiles):
                valid_str = profile.get("valid", "")
                try:
                    valid_time = datetime.fromisoformat(valid_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    log.warning(
                        "ArchiveSoundingFetcher: unparseable valid time %r for %s",
                        valid_str, station_id,
                    )
                    continue
                if valid_time > t:
                    continue
                sounding = _parse_profile(
                    lat, lon, elev, profile.get("profile", []), valid_time, idx
                )
                if sounding is not None:
                    soundings.append(sounding)

            if not soundings:
                emit_alive(self, "fetch_error", f"No valid sounding time slots found for {station_id}")
                return

            deduped: dict[datetime, Sounding] = {}
            for sounding in soundings:
                deduped[sounding.valid_time] = sounding

            ordered = list(deduped.values())
            ordered.sort(key=lambda s: s.valid_time)
            for i, sounding in enumerate(ordered):
                sounding.slot_offset = i
                sounding.label = _format_label(sounding.valid_time)

            sset = SoundingSet(
                lat=lat, lon=lon, elevation=elev,
                fetch_time=t,
                soundings=ordered,
                station_id=station_id,
                station_name=station_id,
                source="obs",
            )
            emit_alive(self, "sounding_ready", sset)
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: obs sounding failed: %s", exc)
            emit_alive(self, "fetch_error", f"Observed sounding error: {exc}")


def _archive_timestamps_to_query(t: datetime) -> list[str]:
    """Match the live obs-sounding query logic, but against the archive clock."""
    archive_time = t.astimezone(timezone.utc)
    dates = [archive_time.date()]
    if archive_time.hour < 12:
        dates.append((archive_time - timedelta(days=1)).date())
    timestamps: list[str] = []
    for date_obj in dates:
        for hour in (0, 6, 12, 18):
            ts = datetime(
                date_obj.year, date_obj.month, date_obj.day, hour, 0, 0,
                tzinfo=timezone.utc,
            )
            timestamps.append(ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return timestamps
