"""Vehicle observations for archive playback, from the ``[archive] vehicle_obs``
sources (archive/vehicle_obs.py): the user's own logger files, or a provider's
(the NSSL FOFS mesonet files)."""

from __future__ import annotations

import bisect
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from archive import vehicle_obs
from archive.session import last_moving_time
from archive.vehicle_speed import VehicleSpeed, calculate_vehicle_speed
from core.observation import Observation
from core.qt_signals import emit_alive

log = logging.getLogger(__name__)

_FRESH_SECONDS = 60


class ArchiveVehicleObsFetcher(QObject):
    """Loads and replays optional one-second observations for archive vehicles."""

    observation_ready = pyqtSignal(object)
    vehicles_cleared = pyqtSignal()
    load_finished = pyqtSignal(object)  # set[str] of MQTT vehicle IDs
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, sources: list | None = None, parent=None):
        super().__init__(parent)
        self._date_str = session_date.strftime("%Y%m%d")
        self._session_day = session_date.date()
        self._sources = vehicle_obs.build_sources() if sources is None else list(sources)
        self._observations: dict[str, list[Observation]] = {}
        self._timestamps: dict[str, list[datetime]] = {}
        self._last_indices: dict[str, int] = {}
        self._last_time: datetime | None = None
        self._loaded = False
        self.roster_size = 0

    @property
    def has_sources(self) -> bool:
        return bool(self._sources)

    @property
    def available_vehicle_ids(self) -> set[str]:
        return set(self._observations)

    def load(self, vehicles: dict[str, str | None]) -> None:
        """Probe and load daily files for MQTT vehicle IDs in a background thread."""
        threading.Thread(
            target=self._load_all,
            args=(dict(vehicles),),
            daemon=True,
        ).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            return
        if self._last_time is not None and archive_time < self._last_time:
            self._last_indices.clear()
            self.vehicles_cleared.emit()
        self._last_time = archive_time

        for vehicle_id in self._observations:
            index = self._index_at(vehicle_id, archive_time)
            if index < 0 or self._last_indices.get(vehicle_id) == index:
                continue
            self._last_indices[vehicle_id] = index
            self.observation_ready.emit(self._observations[vehicle_id][index])

    def resend(self, archive_time: datetime) -> None:
        """Send each vehicle's observation at *archive_time* again."""
        self._last_indices.clear()
        self.on_time_changed(archive_time)

    def has_fresh_observation(self, vehicle_id: str, archive_time: datetime) -> bool:
        """Return whether one-second data should supersede MQTT at this time."""
        index = self._index_at(vehicle_id, archive_time)
        if index < 0:
            return False
        age = (archive_time - self._observations[vehicle_id][index].timestamp).total_seconds()
        return 0 <= age <= _FRESH_SECONDS

    def _with_published_vehicles(self, vehicles: dict[str, str | None]) -> dict[str, str | None]:
        """Add every vehicle a source has files for this session to the given
        roster (MQTT IDs -> icon type). A roster taken from recorded MQTT
        history alone misses vehicles that were never connected to STORM."""
        merged = dict(vehicles)
        for source in self._sources:
            try:
                published = source.vehicles(self._session_day)
            except Exception as exc:  # noqa: BLE001 - the roster just lacks its vehicles
                log.warning("Vehicle obs: %s couldn't list its vehicles: %s", source.name, exc)
                continue
            for vehicle in sorted(published or ()):
                if vehicle not in merged and not any(_same_vehicle(source, vehicle, v) for v in merged):
                    merged[vehicle] = None
        return merged

    def activity_times(self) -> list[datetime]:
        """Each vehicle's last time actually driving -- a mesonet rack left
        logging overnight while parked doesn't count."""
        times = [last_moving_time(observations) for observations in self._observations.values()]
        return [t for t in times if t is not None]

    def observations_by_vehicle(self) -> dict[str, list[Observation]]:
        """Each vehicle's whole session of one-second observations,
        time-ordered (empty until loaded). Not to be modified."""
        return self._observations

    def history(self, vehicle_id: str, through_time: datetime) -> list[Observation]:
        """Return one-second observations for a vehicle through the requested time."""
        index = self._index_at(vehicle_id, through_time)
        if index < 0:
            return []
        return self._observations[vehicle_id][:index + 1]

    def speed(self, vehicle_id: str, archive_time: datetime) -> VehicleSpeed:
        return calculate_vehicle_speed(
            self._observations.get(vehicle_id, []),
            archive_time,
            short_seconds=1,
            average_seconds=15,
        )

    def vehicle_positions_near(self, target_time: datetime) -> list[tuple[str, float, float]]:
        """(vehicle_id, lat, lon) at each vehicle's fix nearest target_time.
        Not the first fix: crews can stage far from the storm and drive for hours."""
        result = []
        for vehicle_id, observations in self._observations.items():
            timestamps = self._timestamps.get(vehicle_id)
            if not observations or not timestamps:
                continue
            i = bisect.bisect_left(timestamps, target_time)
            candidates = [j for j in (i - 1, i) if 0 <= j < len(observations)]
            nearest = observations[min(candidates, key=lambda j: abs(timestamps[j] - target_time))]
            result.append((vehicle_id, nearest.lat, nearest.lon))
        return result

    def _index_at(self, vehicle_id: str, archive_time: datetime) -> int:
        timestamps = self._timestamps.get(vehicle_id)
        if not timestamps:
            return -1
        return bisect.bisect_right(timestamps, archive_time) - 1

    def _load_all(self, vehicles: dict[str, str | None]) -> None:
        vehicles = self._with_published_vehicles(vehicles)
        self.roster_size = len(vehicles)
        loaded: dict[str, list[Observation]] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._fetch_vehicle, vehicle_id, icon_type): vehicle_id
                for vehicle_id, icon_type in vehicles.items()
            }
            for future in as_completed(futures):
                vehicle_id = futures[future]
                try:
                    observations = future.result()
                    if observations:
                        loaded[vehicle_id] = observations
                except Exception as exc:
                    log.warning("One-second archive load failed for %s: %s", vehicle_id, exc)
                    errors.append(f"{vehicle_id}: {exc}")

        self._observations = loaded
        self._timestamps = {
            vehicle_id: [obs.timestamp for obs in observations]
            for vehicle_id, observations in loaded.items()
        }
        self._loaded = True
        if errors and not loaded:
            emit_alive(self, "error", "; ".join(errors))
        emit_alive(self, "load_finished", set(loaded))

    def _fetch_vehicle(self, vehicle_id: str, icon_type: str | None) -> list[Observation]:
        """The first source with rows for this vehicle in the session, as one
        clean track. A source that fails is skipped; if every source either
        failed or had nothing and one failed, this raises."""
        errors = []
        for source in self._sources:
            try:
                rows = source.load(vehicle_id, icon_type, self._session_day)
            except Exception as exc:  # noqa: BLE001 - try the next source
                log.debug("Vehicle obs: %s failed for %s: %s", source.name, vehicle_id, exc)
                errors.append(f"{source.name}: {exc}")
                continue
            rows = vehicle_obs.in_session(rows or [], self._session_day)
            if not rows:
                continue
            kept, conflicts, glitches = vehicle_obs.cleanup_track(rows)
            log.info(
                "One-second archive: loaded %d observations for %s on %s from %s "
                "(%d seconds with conflicting positions resolved, %d glitch fixes dropped)",
                len(kept), vehicle_id, self._date_str, source.name, conflicts, glitches,
            )
            return kept
        if errors:
            raise RuntimeError("; ".join(errors))
        return []


def _same_vehicle(source, a: str, b: str) -> bool:
    """Whether a source files two IDs under one vehicle (a provider's aliases)."""
    same = getattr(source, "same_vehicle", None)
    return bool(same and same(a, b))
