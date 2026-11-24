
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from archive import history
from core.annotation import Annotation
from core.storm_cone import StormCone
from core.drawing import DrawingAnnotation
from core.scan_sector import ScanSector
from network.vehicle_sync import _observation_from_payload
from archive.session import last_moving_time, session_bounds
from archive.vehicle_speed import VehicleSpeed, calculate_vehicle_speed
from core.qt_signals import emit_alive

log = logging.getLogger(__name__)

_TOPICS = ("vehicles", "annotations", "cones", "drawings", "scan_sectors")


def _parse_timestamp(obj: dict) -> Optional[datetime]:
    """Extract a UTC datetime from a JSONL record."""
    # scan sectors
    scan_ts = obj.get("timestamp")
    if scan_ts:
        try:
            return datetime.fromisoformat(
                scan_ts.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass

    # annotations / cones / drawings
    ts_str = obj.get("created_at") or obj.get("deleted_at")
    if ts_str:
        try:
            return datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    # vehicles
    gps_date = obj.get("gps_date")
    gps_time = obj.get("gps_time")
    if gps_date and gps_time:
        try:
            return datetime.strptime(
                str(gps_date) + str(gps_time), "%d%m%y%H%M%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_jsonl(text: str) -> list[tuple[datetime, dict]]:
    """Parse JSONL into a sorted list of (timestamp, obj) tuples, skipping unparseable lines."""
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            ts = _parse_timestamp(obj)
            if ts is not None:
                results.append((ts, obj))
        except Exception:
            continue
    results.sort(key=lambda x: x[0])
    return results


def _expired(obj: dict, t: datetime) -> bool:
    """Whether a record's expires_at is at or before t (no expiry: never)."""
    text = obj.get("expires_at")
    if not text:
        return False
    try:
        expires = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= t


class ArchiveMQTTReader(QObject):
    """
    Fetches recorded broker traffic (archive/history.py: the sources in
    [archive] history) and replays
    vehicle positions, annotations, cones, drawings, and scan sectors in sync with
    the archive TimeController.

    Signals
    -------
    vehicle_position(str, float, float, float, float)
        vehicle_id, lat, lon, speed_ms, heading — emitted for each vehicle
        at the current archive time.
    vehicles_cleared()
        Emitted when the archive time jumps backward.
    annotation_received(Annotation)
    annotation_deleted(str, str)       annotation_id, deleted_at
    cone_received(StormCone)
    cone_deleted(str)                  cone_id
    drawing_received(DrawingAnnotation)
    drawing_deleted(str)               drawing_id
    scan_sector_received(ScanSector)
    scan_sectors_cleared()
    error(str)
    """

    vehicle_position    = pyqtSignal(object)  # Observation
    vehicles_cleared    = pyqtSignal()
    annotation_received = pyqtSignal(object)
    annotation_deleted  = pyqtSignal(str, str)
    cone_received       = pyqtSignal(object)
    cone_deleted        = pyqtSignal(str)
    drawing_received    = pyqtSignal(object)
    drawing_deleted     = pyqtSignal(str)
    scan_sector_received = pyqtSignal(object)
    scan_sectors_cleared = pyqtSignal()
    error               = pyqtSignal(str)
    loaded              = pyqtSignal()   # main thread, once every topic has been fetched
    _load_complete      = pyqtSignal()   # internal — fires on bg thread, triggers main-thread replay

    def __init__(self, session_date: datetime, sources: list | None = None, parent=None,
                 renames: dict[str, str] | None = None):
        super().__init__(parent)
        # the [archive] history sources, tried in order for each file
        self._sources = history.build_sources() if sources is None else sources
        # vehicle IDs as recorded -> as shown (a provider's VEHICLE_RENAMES:
        # NSSL's pre-3.0 lid1 -> dltruck)
        self._renames = dict(renames or {})
        self._date_str = session_date.strftime("%Y%m%d")
        # the session runs into the next UTC morning (archive/session.py),
        # whose records are in the next day's files
        start, self._cap = session_bounds(session_date)
        self._next_date_str = (start + timedelta(days=1)).strftime("%Y%m%d")
        self._data: dict[str, list[tuple[datetime, dict]]] = {t: [] for t in _TOPICS}
        self._loaded = False
        self._vehicle_observations: dict[str, list] = {}
        self._pending_time: Optional[datetime] = None
        self._last_emit_time: Optional[datetime] = None
        # what's on the map now, per topic: id -> the record it was drawn from
        self._shown: dict[str, dict[str, dict]] = {"annotations": {}, "cones": {}, "drawings": {}}
        # _load_complete is queued automatically by Qt (bg thread → main thread)
        self._load_complete.connect(self._on_load_complete)


    def load(self) -> None:
        """Fetch all archive files for the session date (background thread)."""
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            self._pending_time = archive_time
            return

        # backward jump -- vehicles and scan sectors are re-sent from scratch
        # (annotations, cones and drawings are brought to the new time below)
        if self._last_emit_time is not None and archive_time < self._last_emit_time:
            self.vehicles_cleared.emit()
            self.scan_sectors_cleared.emit()

        self._last_emit_time = archive_time
        self._emit_vehicles(archive_time)
        self._sync_items("annotations", archive_time)
        self._sync_items("cones", archive_time)
        self._sync_items("drawings", archive_time)
        self._emit_scan_sectors(archive_time)

    def resend(self, archive_time: datetime) -> None:
        """Send the vehicles and scan sectors at *archive_time* again (a
        drawer turning a provider's vehicles back on)."""
        self._last_emit_time = None
        self.on_time_changed(archive_time)

    def vehicle_positions_near(self, target_time: datetime) -> list[tuple[str, float, float]]:
        """Return (vehicle_id, lat, lon) tuples from the vehicle timestamp
        closest to target_time.

        Deliberately not the day's first GPS fix -- a deployment can stage
        from a base far from the actual storm intercept and drive for hours
        before it matters, so "nearest radar to the day's first position"
        can point at entirely the wrong station. target_time should be the
        archive session's requested start time, which is a much better
        proxy for where the deployment actually was.
        """
        msgs = self._data.get("vehicles", [])
        if not msgs:
            return []
        nearest_time = min((ts for ts, _ in msgs), key=lambda ts: abs(ts - target_time))
        result = []
        for ts, obj in msgs:
            if ts != nearest_time:
                continue
            try:
                obs = _observation_from_payload(obj)
                result.append((obs.vehicle_id, obs.lat, obs.lon))
            except Exception:
                continue
        return result

    def vehicle_metadata(self) -> dict[str, str | None]:
        """Return vehicle IDs and their first advertised icon type."""
        vehicles: dict[str, str | None] = {}
        for _, obj in self._data.get("vehicles", []):
            vehicle_id = obj.get("vehicle_id")
            if not vehicle_id:
                continue
            icon_type = (obj.get("icon_type") or "").strip() or None
            if vehicle_id not in vehicles or vehicles[vehicle_id] is None:
                vehicles[vehicle_id] = icon_type
        return vehicles

    def vehicle_speed(self, vehicle_id: str, archive_time: datetime) -> VehicleSpeed:
        """Return MQTT-cadence ground speeds for a vehicle."""
        return calculate_vehicle_speed(
            self._vehicle_observations.get(vehicle_id, []),
            archive_time,
            short_seconds=10,
            average_seconds=30,
        )

    def vehicle_histories(self) -> dict[str, list]:
        """Each vehicle's whole recorded day of observations, time-ordered
        (empty until loaded). Not to be modified."""
        return self._vehicle_observations

    def activity_times(self) -> list[datetime]:
        """Evidence of the crew working, for sizing the session: the last
        scan-sector/annotation/cone/drawing change, and each vehicle's last
        time actually driving (a parked vehicle keeps reporting)."""
        times = [rows[-1][0] for topic, rows in self._data.items() if topic != "vehicles" and rows]
        times += [last_moving_time(history) for history in self._vehicle_observations.values()]
        return [t for t in times if t is not None]

    def _on_load_complete(self) -> None:
        """Called on the main thread once background fetch finishes."""
        histories: dict[str, list] = {}
        for _, obj in self._data.get("vehicles", []):
            try:
                observation = _observation_from_payload(obj)
            except Exception:
                continue
            histories.setdefault(observation.vehicle_id, []).append(observation)
        self._vehicle_observations = histories
        self._loaded = True
        if self._pending_time is not None:
            self.on_time_changed(self._pending_time)
            self._pending_time = None
        self.loaded.emit()


    def _fetch_all(self) -> None:
        errors = []
        # topics are independent, and a miss on one source plus the next can
        # take 10 s or more each (NSSL's THREDDS then API), so fetch them all
        # at once, with the next morning's files alongside the session day's
        with ThreadPoolExecutor(max_workers=2 * len(_TOPICS)) as pool:
            futures = {
                pool.submit(history.fetch_text, self._sources, topic, date_str): (topic, date_str)
                for topic in _TOPICS
                for date_str in (self._date_str, self._next_date_str)
            }
            records: dict[str, list] = {topic: [] for topic in _TOPICS}
            for future, (topic, date_str) in futures.items():
                try:
                    text, source = future.result()
                    if text:
                        parsed = _parse_jsonl(text)
                        if self._renames:
                            for _, obj in parsed:
                                vid = obj.get("vehicle_id")
                                if isinstance(vid, str) and vid.strip().lower() in self._renames:
                                    obj["vehicle_id"] = self._renames[vid.strip().lower()]
                        if date_str == self._next_date_str:
                            parsed = [(ts, obj) for ts, obj in parsed if ts <= self._cap]
                        records[topic] += parsed
                        log.info(
                            "ArchiveMQTTReader: loaded %d %s records for %s (%s)",
                            len(parsed), topic, date_str, source,
                        )
                    else:
                        log.info("ArchiveMQTTReader: no %s file for %s", topic, date_str)
                except Exception as exc:
                    if date_str == self._next_date_str:
                        # the next morning is extra; don't fail the session over it
                        log.warning("ArchiveMQTTReader: fetch failed for %s %s: %s", topic, date_str, exc)
                        continue
                    log.error("ArchiveMQTTReader: fetch failed for %s: %s", topic, exc)
                    errors.append(f"{topic}: {exc}")
            for topic, rows in records.items():
                self._data[topic] = sorted(rows, key=lambda item: item[0])

        self._loaded = False  # set True on main thread via _load_complete signal
        if errors:
            emit_alive(self, "error", f"Archive MQTT load errors: {'; '.join(errors)}")
        emit_alive(self, "_load_complete")


    def _emit_vehicles(self, t: datetime) -> None:
        """Emit the most recent position for each vehicle at or before t."""
        latest: dict[str, dict] = {}
        for ts, obj in self._data["vehicles"]:
            if ts > t:
                break
            vid = obj.get("vehicle_id")
            if vid:
                latest[vid] = obj
        for obj in latest.values():
            try:
                obs = _observation_from_payload(obj)
                self.vehicle_position.emit(obs)
            except Exception as exc:
                log.debug("ArchiveMQTTReader: vehicle parse error: %s", exc)

    def items_at(self, topic: str, t: datetime) -> dict[str, dict]:
        """The annotations, cones or drawings that existed at time t, as
        id -> record: each one from when it was issued (created_at), in its
        latest version as of t (the crew edits by republishing the same id),
        until it was deleted or expired (expires_at -- cones last an hour)."""
        state: dict[str, dict] = {}
        for ts, obj in self._data[topic]:
            if ts > t:
                break
            item_id = obj.get("id", "")
            if not item_id:
                continue
            if obj.get("deleted"):
                state.pop(item_id, None)
            else:
                state[item_id] = obj
        return {item_id: obj for item_id, obj in state.items() if not _expired(obj, t)}

    def _sync_items(self, topic: str, t: datetime) -> None:
        """Bring the map's annotations, cones or drawings to time t: remove
        what no longer exists, add or update the rest."""
        make, received, deleted = {
            "annotations": (Annotation.from_dict, self.annotation_received,
                            lambda item_id: self.annotation_deleted.emit(item_id, "")),
            "cones": (StormCone.from_dict, self.cone_received, self.cone_deleted.emit),
            "drawings": (DrawingAnnotation.from_dict, self.drawing_received, self.drawing_deleted.emit),
        }[topic]
        wanted, shown = self.items_at(topic, t), self._shown[topic]
        for item_id in [i for i in shown if i not in wanted]:
            del shown[item_id]
            deleted(item_id)
        for item_id, obj in wanted.items():
            if shown.get(item_id) is obj:
                continue
            try:
                item = make(obj)
            except Exception as exc:
                log.debug("ArchiveMQTTReader: %s parse error: %s", topic, exc)
                continue
            shown[item_id] = obj
            received.emit(item)

    def _emit_scan_sectors(self, t: datetime) -> None:
        """Emit the most recent scan-sector state for each vehicle at or before t."""
        latest: dict[str, dict] = {}
        for ts, obj in self._data["scan_sectors"]:
            if ts > t:
                break
            vid = obj.get("vehicle_id")
            if vid:
                latest[vid] = obj
        for obj in latest.values():
            try:
                self.scan_sector_received.emit(ScanSector.from_dict(obj))
            except Exception as exc:
                log.debug("ArchiveMQTTReader: scan sector parse error: %s", exc)
