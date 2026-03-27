# archive/fetchers/mqtt_reader.py
# ArchiveMQTTReader — reads stored MQTT message files and replays vehicle
# positions and annotations in sync with the archive time controller.
#
# File layout (to be configured once the server is set up):
#   {base_url}/{YYYY-MM-DD}/{topic_slug}.jsonl
#
# Each line of a JSONL file is a JSON object with at minimum:
#   { "timestamp": "<ISO-8601 UTC>", "topic": "...", "payload": { ... } }
#
# Message schema is intentionally stubbed here — it will be filled in once
# the actual MQTT subscriber format is confirmed.  The reader already handles
# all the time-filtering, caching, and position interpolation logic; only the
# schema constants at the bottom of this file need updating.

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

# ── Schema stubs — update once the subscriber format is confirmed ─────────────
# These are the JSONL topic slugs expected under {base_url}/{date}/
TOPIC_VEHICLES     = "vehicles"          # e.g. "storm/vehicles/#"
TOPIC_ANNOTATIONS  = "annotations"      # e.g. "storm/annotations/#"

# Field names within the parsed payload dict.
# Vehicle position message:
FIELD_VEHICLE_ID   = "vehicle_id"       # str
FIELD_LAT          = "lat"              # float
FIELD_LON          = "lon"              # float
FIELD_SPEED        = "speed"            # float, knots (optional)
FIELD_HEADING      = "heading"          # float, degrees (optional)

# ── Reader ────────────────────────────────────────────────────────────────────


class ArchiveMQTTReader(QObject):
    """
    Reads and replays MQTT archive files stored as JSONL by UTC day.

    Signals
    -------
    vehicle_position(str, float, float, float, float)
        vehicle_id, lat, lon, speed, heading — emitted for each vehicle at the
        current archive time.
    vehicles_cleared()
        Emitted when the archive time jumps backward (all vehicle markers
        should be removed).
    error(str)
    """

    vehicle_position  = pyqtSignal(str, float, float, float, float)
    vehicles_cleared  = pyqtSignal()
    error             = pyqtSignal(str)

    def __init__(self, base_url: str, session_date: datetime, parent=None):
        """
        Parameters
        ----------
        base_url : str
            Root URL for the MQTT archive file server.
            Files are fetched from {base_url}/{YYYY-MM-DD}/{topic}.jsonl
        session_date : datetime
            UTC date of the archive session.
        """
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._date     = session_date
        self._date_str = session_date.strftime("%Y-%m-%d")

        # Raw messages sorted by timestamp: list of (datetime, topic, payload).
        self._vehicle_msgs: list[tuple] = []
        self._loaded = False
        self._load_lock = threading.Lock()

        # Last emitted timestamp to detect backwards jumps.
        self._last_emit_time: Optional[datetime] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Fetch JSONL file(s) for the session date (background thread)."""
        if not self._base_url:
            log.info("ArchiveMQTTReader: no base_url configured — MQTT replay disabled")
            self._loaded = True
            return
        threading.Thread(target=self._fetch_files, daemon=True).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """Called by TimeController on every tick.  Emits positions at archive_time."""
        if not self._loaded:
            return

        # Detect backwards jump — clear all markers.
        if (
            self._last_emit_time is not None
            and archive_time < self._last_emit_time
        ):
            self.vehicles_cleared.emit()

        self._last_emit_time = archive_time
        self._emit_positions_at(archive_time)

    def nearest_vehicle_position_time(self, archive_time: datetime) -> Optional[datetime]:
        """
        Return the timestamp of the first vehicle message in the file.
        Used by the main window to auto-select the nearest radar station.
        """
        if not self._vehicle_msgs:
            return None
        return self._vehicle_msgs[0][0]

    def first_vehicle_positions(self) -> list:
        """
        Return a list of (vehicle_id, lat, lon) for the first timestamp in the
        vehicle message stream.  Used to pick the initial radar station.
        """
        if not self._vehicle_msgs:
            return []
        first_time = self._vehicle_msgs[0][0]
        result = []
        for ts, topic, payload in self._vehicle_msgs:
            if ts > first_time:
                break
            vid = payload.get(FIELD_VEHICLE_ID, "unknown")
            lat = payload.get(FIELD_LAT)
            lon = payload.get(FIELD_LON)
            if lat is not None and lon is not None:
                result.append((vid, float(lat), float(lon)))
        return result

    # ── File loading ──────────────────────────────────────────────────────────

    def _fetch_files(self) -> None:
        """Read vehicle position JSONL file for the session date (local or HTTP)."""
        rel_path = f"{self._date_str}/{TOPIC_VEHICLES}.jsonl"
        try:
            text = _read_file_snapshot(self._base_url, rel_path)
            if text is None:
                log.info("ArchiveMQTTReader: no vehicle file for %s", self._date_str)
                return
            msgs = _parse_jsonl(text)
            self._vehicle_msgs = sorted(msgs, key=lambda x: x[0])
            log.info(
                "ArchiveMQTTReader: loaded %d vehicle messages for %s",
                len(self._vehicle_msgs), self._date_str,
            )
        except Exception as exc:
            log.error("ArchiveMQTTReader: load failed: %s", exc)
            self.error.emit(f"MQTT archive load failed: {exc}")
        finally:
            with self._load_lock:
                self._loaded = True

    # ── Position emission ─────────────────────────────────────────────────────

    def _emit_positions_at(self, t: datetime) -> None:
        """Emit the most recent position for each vehicle at or before time t."""
        if not self._vehicle_msgs:
            return

        # Find all messages up to time t.
        # Build a dict of vehicle_id → latest message.
        latest: dict[str, dict] = {}
        for ts, topic, payload in self._vehicle_msgs:
            if ts > t:
                break
            vid = payload.get(FIELD_VEHICLE_ID)
            if vid:
                latest[vid] = payload

        for vid, payload in latest.items():
            lat  = payload.get(FIELD_LAT)
            lon  = payload.get(FIELD_LON)
            spd  = float(payload.get(FIELD_SPEED, 0) or 0)
            hdg  = float(payload.get(FIELD_HEADING, 0) or 0)
            if lat is not None and lon is not None:
                self.vehicle_position.emit(vid, float(lat), float(lon), spd, hdg)


# ── File reader ───────────────────────────────────────────────────────────────

def _read_file_snapshot(base: str, rel_path: str) -> Optional[str]:
    """
    Return the full text content of a JSONL file as a single string snapshot,
    then close it immediately — so concurrent appends by an MQTT logger never
    cause a conflict or partial-read issue.

    Supports:
      - Local directory paths  e.g. "/data/mqtt"
      - file:// URLs           e.g. "file:///data/mqtt"
      - HTTP/HTTPS URLs        e.g. "http://localhost:9000"

    Returns None when the file does not exist (404 or missing path).
    Raises on unexpected errors so the caller can log them.
    """
    if base.startswith("file://"):
        local_dir = base[len("file://"):]
        path = Path(local_dir) / rel_path
        if not path.exists():
            return None
        # Read entire file at once, then release the handle immediately.
        return path.read_text(encoding="utf-8")

    if not base.startswith("http://") and not base.startswith("https://"):
        # Treat as a plain local directory path.
        path = Path(base) / rel_path
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # HTTP/HTTPS — requests buffers the full response body before returning.
    url = f"{base}/{rel_path}"
    resp = requests.get(url, timeout=30, stream=False)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


# ── JSONL parser ──────────────────────────────────────────────────────────────

def _parse_jsonl(text: str) -> list:
    """
    Parse a JSONL string into a list of (datetime, topic, payload) tuples.
    Silently skips malformed lines.
    """
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            ts_str = obj.get("timestamp") or obj.get("ts")
            topic  = obj.get("topic", "")
            payload = obj.get("payload", obj)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    pass
            if ts_str:
                ts = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                results.append((ts, topic, payload))
        except Exception:
            continue
    return results
