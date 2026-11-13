
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from archive.fetchers.clamps_sonde_archive_fetcher import TruckPosition, fetch_clamps_sonde_soundings
from archive.fetchers.clamps_tropoe_archive_fetcher import fetch_clamps_tropoe_soundings
from archive.fetchers.coptersonde_archive_fetcher import fetch_coptersonde_soundings
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
    On-demand sounding fetcher for archive mode.

    Observed radiosondes (IEM RAOB), NSSL / CLAMPS profiles (lidar truck
    sondes, then TROPoe retrievals) and PERiLS CopterSonde ascents at the
    archive clock rather than "now". There is no archive model sounding:
    Open-Meteo's historical HRRR came back empty or timed out (2026-11-11).

    Signals
    -------
    sounding_ready(SoundingSet)
    coptersondes_ready(dict)    {site + tail number: SoundingSet}
    fetch_error(str)
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    coptersondes_ready = pyqtSignal(object)   # {site: SoundingSet}
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_archive_time: Optional[datetime] = None
        self._truck_position: TruckPosition | None = None


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

    def set_truck_position(self, lookup: TruckPosition | None) -> None:
        """Where the lidar truck was at a time (within a minute), to place
        its sonde launches. Called from worker threads."""
        self._truck_position = lookup

    def fetch_coptersondes(self) -> None:
        """PERiLS CopterSonde ascents on the clock's day, up to the clock."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(target=self._do_fetch_coptersondes, args=(t,), daemon=True).start()

    def fetch_nssl_sounding(self) -> None:
        """Fetch the NSSL mobile sounding nearest to the current archive time."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_nssl,
            args=(t,),
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


    def _do_fetch_nssl(self, t: datetime) -> None:
        """Try the lidar truck's sondes on THREDDS, then the NSSL API, then
        CLAMPS TROPoe retrievals (clearly labeled as such). One provider's
        failure doesn't stop the next. Every result is limited to the clock's
        UTC day, up to the clock."""
        errors, empty = [], []
        for name, fetch in (
            ("THREDDS sonde", lambda: fetch_clamps_sonde_soundings(t, self._truck_position)),
            ("NSSL API", lambda: _api_sonde_set(t)),
            ("CLAMPS TROPoe", lambda: fetch_clamps_tropoe_soundings(t)),
        ):
            try:
                sset = _within_day(fetch(), t)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                log.warning("Archive sounding provider failed: %s: %s", name, exc)
                continue
            if sset is not None:
                emit_alive(self, "sounding_ready", sset)
                return
            empty.append(name)
        parts = []
        if empty:
            parts.append(f"none from {', '.join(empty)} by {t:%H:%MZ} on {t:%Y-%m-%d}")
        detail = "; ".join(parts + errors)
        emit_alive(self, "fetch_error", f"NSSL / CLAMPS sounding unavailable: {detail}")

    def _do_fetch_coptersondes(self, t: datetime) -> None:
        try:
            sets = fetch_coptersonde_soundings(t)
        except Exception as exc:  # noqa: BLE001
            log.warning("CopterSonde fetch failed: %s", exc)
            emit_alive(self, "fetch_error", f"CopterSonde: {exc}")
            return
        emit_alive(self, "coptersondes_ready", sets)


def _api_sonde_set(t: datetime) -> SoundingSet | None:
    """The NSSL API's launches on the clock's day (its index is rolling and
    of unknown depth, and the API answered 404 on 2026-11-11)."""
    from data.fetchers.clamps_sounding_fetcher import (
        _api_sonde_entries,
        _fetch_and_parse_url,
        _soundings_to_set,
    )
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    entries = sorted((e for e in _api_sonde_entries() if day_start <= e.file_time <= t),
                     key=lambda e: e.file_time)
    soundings = []
    for idx, entry in enumerate(entries):
        try:
            snd = _fetch_and_parse_url(entry.skewt_url, entry.file_time, idx, entry.raw_url)
        except Exception as exc:  # noqa: BLE001 - skip this launch
            log.warning("ArchiveSoundingFetcher: NSSL API file %s failed: %s", entry.skewt_url, exc)
            continue
        if snd is not None:
            soundings.append(snd)
    return _soundings_to_set(soundings, t) if soundings else None


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
