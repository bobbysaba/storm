# archive/fetchers/mqtt_reader.py
# ArchiveMQTTReader — fetches and replays STORM archive files from NSSL THREDDS.
#
# File layout (NSSL THREDDS fileServer):
#   {base_url}/storm.vehicles.{YYYYMMDD}     — append-only position log
#   {base_url}/storm.annotations.{YYYYMMDD}  — keyed by id; deletes have deleted_at
#   {base_url}/storm.cones.{YYYYMMDD}        — same schema as annotations
#   {base_url}/storm.drawings.{YYYYMMDD}     — same schema as annotations
#
# Each line is a JSON object.  Vehicles use gps_date/gps_time for timestamps;
# annotations/cones/drawings use created_at (creates) or deleted_at (deletes).

import json
import logging
import ssl
import threading
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from core.annotation import Annotation
from core.storm_cone import StormCone
from core.drawing import DrawingAnnotation
from network.vehicle_sync import _observation_from_payload

log = logging.getLogger(__name__)

_THREDDS_FILESERVER = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/storm"
_TOPICS = ("vehicles", "annotations", "cones", "drawings")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_text(url: str) -> Optional[str]:
    """Fetch a THREDDS file as text; return None on 404, raise on other errors."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
        with urlopen(req, timeout=20, context=_ssl_ctx()) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        if "404" in str(exc) or "HTTP Error 404" in str(exc):
            return None
        raise


def _parse_timestamp(obj: dict) -> Optional[datetime]:
    """Extract a UTC datetime from a JSONL record using created_at or gps_date/gps_time."""
    # Annotations / cones / drawings
    ts_str = obj.get("created_at") or obj.get("deleted_at")
    if ts_str:
        try:
            return datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    # Vehicles
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


class ArchiveMQTTReader(QObject):
    """
    Fetches STORM archive JSONL files from NSSL THREDDS and replays
    vehicle positions, annotations, cones, and drawings in sync with
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
    error(str)
    """

    vehicle_position    = pyqtSignal(str, float, float, float, float)
    vehicles_cleared    = pyqtSignal()
    annotation_received = pyqtSignal(object)
    annotation_deleted  = pyqtSignal(str, str)
    cone_received       = pyqtSignal(object)
    cone_deleted        = pyqtSignal(str)
    drawing_received    = pyqtSignal(object)
    drawing_deleted     = pyqtSignal(str)
    error               = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date_str = session_date.strftime("%Y%m%d")
        self._data: dict[str, list[tuple[datetime, dict]]] = {t: [] for t in _TOPICS}
        self._loaded = False
        self._last_emit_time: Optional[datetime] = None
        # Track which annotation/cone/drawing ids have been emitted so we can
        # detect backward jumps and re-emit from scratch.
        self._emitted_ids: dict[str, set[str]] = {
            "annotations": set(), "cones": set(), "drawings": set()
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Fetch all archive files for the session date (background thread)."""
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            return

        # Backward jump — clear all state and re-emit from scratch.
        if self._last_emit_time is not None and archive_time < self._last_emit_time:
            self.vehicles_cleared.emit()
            self._emitted_ids = {"annotations": set(), "cones": set(), "drawings": set()}

        self._last_emit_time = archive_time
        self._emit_vehicles(archive_time)
        self._emit_annotations(archive_time)
        self._emit_cones(archive_time)
        self._emit_drawings(archive_time)

    def first_vehicle_positions(self) -> list[tuple[str, float, float]]:
        """Return (vehicle_id, lat, lon) tuples from the first vehicle timestamp."""
        msgs = self._data.get("vehicles", [])
        if not msgs:
            return []
        first_time = msgs[0][0]
        result = []
        for ts, obj in msgs:
            if ts > first_time:
                break
            try:
                obs = _observation_from_payload(obj)
                result.append((obs.vehicle_id, obs.lat, obs.lon))
            except Exception:
                continue
        return result

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _fetch_all(self) -> None:
        errors = []
        for topic in _TOPICS:
            url = f"{_THREDDS_FILESERVER}/storm.{topic}.{self._date_str}"
            try:
                text = _fetch_text(url)
                if text:
                    self._data[topic] = _parse_jsonl(text)
                    log.info(
                        "ArchiveMQTTReader: loaded %d %s records",
                        len(self._data[topic]), topic,
                    )
                else:
                    log.info("ArchiveMQTTReader: no %s file for %s", topic, self._date_str)
            except Exception as exc:
                log.error("ArchiveMQTTReader: fetch failed for %s: %s", topic, exc)
                errors.append(f"{topic}: {exc}")

        self._loaded = True
        if errors:
            self.error.emit(f"Archive MQTT load errors: {'; '.join(errors)}")

    # ── Emission helpers ──────────────────────────────────────────────────────

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
                self.vehicle_position.emit(
                    obs.vehicle_id, obs.lat, obs.lon,
                    obs.wind_speed_ms or 0.0,
                    obs.wind_dir_deg or 0.0,
                )
            except Exception as exc:
                log.debug("ArchiveMQTTReader: vehicle parse error: %s", exc)

    def _emit_annotations(self, t: datetime) -> None:
        """Apply annotation creates/deletes up to time t."""
        for ts, obj in self._data["annotations"]:
            if ts > t:
                break
            ann_id = obj.get("id", "")
            if not ann_id:
                continue
            if obj.get("deleted"):
                if ann_id in self._emitted_ids["annotations"]:
                    self._emitted_ids["annotations"].discard(ann_id)
                    self.annotation_deleted.emit(ann_id, obj.get("deleted_at", ""))
            elif ann_id not in self._emitted_ids["annotations"]:
                try:
                    ann = Annotation.from_dict(obj)
                    self._emitted_ids["annotations"].add(ann_id)
                    self.annotation_received.emit(ann)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: annotation parse error: %s", exc)

    def _emit_cones(self, t: datetime) -> None:
        """Apply cone creates/deletes up to time t."""
        for ts, obj in self._data["cones"]:
            if ts > t:
                break
            cone_id = obj.get("id", "")
            if not cone_id:
                continue
            if obj.get("deleted"):
                if cone_id in self._emitted_ids["cones"]:
                    self._emitted_ids["cones"].discard(cone_id)
                    self.cone_deleted.emit(cone_id)
            elif cone_id not in self._emitted_ids["cones"]:
                try:
                    cone = StormCone.from_dict(obj)
                    self._emitted_ids["cones"].add(cone_id)
                    self.cone_received.emit(cone)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: cone parse error: %s", exc)

    def _emit_drawings(self, t: datetime) -> None:
        """Apply drawing creates/deletes up to time t."""
        for ts, obj in self._data["drawings"]:
            if ts > t:
                break
            drawing_id = obj.get("id", "")
            if not drawing_id:
                continue
            if obj.get("deleted"):
                if drawing_id in self._emitted_ids["drawings"]:
                    self._emitted_ids["drawings"].discard(drawing_id)
                    self.drawing_deleted.emit(drawing_id)
            elif drawing_id not in self._emitted_ids["drawings"]:
                try:
                    drawing = DrawingAnnotation.from_dict(obj)
                    self._emitted_ids["drawings"].add(drawing_id)
                    self.drawing_received.emit(drawing)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: drawing parse error: %s", exc)
