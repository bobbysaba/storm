# data/obs_file_watcher.py
# Track A — polls the current day's instrument logger file for new rows.
#
# File naming convention: YYYYMMDD.txt inside a configured directory.
# At midnight a new file begins; the watcher rolls over automatically.
#
# Timestamp is two columns: gps_date (DDMMYY) + gps_time (HHMMSS).
# Vehicle identity has no column in the file — always taken from config.
#
# Runs on a QTimer in the main thread (file I/O at 10 s intervals is
# too brief to justify a background thread).

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)


@dataclass
class FieldMap:
    """
    Maps instrument-logger CSV column names to Observation fields.

    Defaults match the FOFS truck logger format (YYYYMMDD.txt):
        sfc_wspd, sfc_wdir, t_fast, dewpoint, pressure,
        gps_date (DDMMYY), gps_time (HHMMSS), lat, lon

    Override any field by passing kwargs to the constructor.
    If your logger writes a single ISO-style timestamp column instead of
    split date+time columns, set date_col="" and time_col="" and set
    timestamp_col to that column name.
    """
    lat:           str = "lat"
    lon:           str = "lon"
    # split date + time columns (default — matches FOFS truck logger)
    date_col:      str = "gps_date"    # DDMMYY  e.g. "050625"
    time_col:      str = "gps_time"    # HHMMSS  e.g. "175228"
    # single combined timestamp column (leave empty to use date_col+time_col)
    timestamp_col: str = ""
    # met fields
    temperature_c: str = "t_fast"
    dewpoint_c:    str = "dewpoint"
    wind_speed_ms: str = "sfc_wspd"
    wind_dir_deg:  str = "sfc_wdir"
    pressure_mb:   str = "pressure"


class ObsFileWatcher(QObject):
    """
    Watches today's YYYYMMDD.txt inside a directory for new rows and
    emits obs_ready for each one.  Rolls over to the next day's file
    automatically at midnight.

    Usage:
        watcher = ObsFileWatcher(
            data_dir="veh_data",
            vehicle_id=config.VEHICLE_ID,
        )
        watcher.obs_ready.connect(main_window.update_vehicle_obs)
        watcher.start()
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, data_dir: str,
                 vehicle_id: str,
                 field_map: FieldMap | None = None,
                 poll_interval_s: int = 10,
                 parent=None):
        super().__init__(parent)
        self._data_dir   = Path(data_dir)
        self._vehicle_id = vehicle_id
        self._fields     = field_map or FieldMap()
        self._poll_ms    = poll_interval_s * 1000

        self._current_date: date | None = None
        self._current_path: Path | None = None
        self._last_mtime: float = 0.0
        self._last_size:  int   = 0
        self._last_pos:   int   = 0
        # cached header columns — read once per file, avoids a separate open()
        # on every mid-file poll just to get column names for DictReader
        self._header_cache: list[str] | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        self._roll_to_today()
        self._timer.start(self._poll_ms)
        log.info("ObsFileWatcher: watching %s every %ds",
                 self._data_dir, self._poll_ms // 1000)

    def stop(self):
        self._timer.stop()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _today_path(self) -> Path:
        return self._data_dir / datetime.now(timezone.utc).strftime("%Y%m%d.txt")

    def _roll_to_today(self):
        """Switch to today's file, resetting byte position."""
        today = datetime.now(timezone.utc).date()
        if today == self._current_date:
            return
        self._current_date  = today
        self._current_path  = self._today_path()
        self._last_mtime    = 0.0
        self._last_size     = 0
        self._last_pos      = 0
        self._header_cache  = None   # reset header cache for the new file
        log.info("ObsFileWatcher: active file → %s", self._current_path)

    def _poll(self):
        # check for date rollover first
        self._roll_to_today()

        path = self._current_path
        if path is None or not path.exists():
            return

        try:
            stat = path.stat()
        except OSError:
            return

        mtime = stat.st_mtime
        size  = stat.st_size

        if mtime == self._last_mtime:
            return   # nothing changed

        self._last_mtime = mtime

        # file shrank → it was rewritten; restart from top
        if size < self._last_size:
            log.debug("ObsFileWatcher: file shrank, resetting position")
            self._last_pos = 0

        self._last_size = size

        try:
            with path.open("rb") as fh:
                fh.seek(self._last_pos)
                chunk = fh.read()
        except OSError as e:
            log.warning("ObsFileWatcher: read error: %s", e)
            return

        if not chunk:
            return

        # Guard against a partially-written trailing row.  The logger writes
        # one row per second; we poll every 10 s.  If we happen to read exactly
        # while a row is mid-write, the last bytes in the chunk may not end with
        # a newline.  Only parse up to — and including — the last complete line,
        # and advance the file position only that far.  The trailing partial bytes
        # will be picked up on the next poll once the write completes.
        last_nl = chunk.rfind(b'\n')
        if last_nl == -1:
            # No complete line in the new bytes yet; wait for next poll.
            return
        chunk = chunk[:last_nl + 1]
        new_pos = self._last_pos + last_nl + 1

        text = chunk.decode("utf-8", errors="replace")

        if self._last_pos == 0:
            # reading from the top — header row is present in the chunk;
            # DictReader will parse it automatically and cache it for later polls
            rows = list(csv.DictReader(io.StringIO(text)))
            # also warm the header cache so mid-file polls don't need a disk read
            if rows:
                self._header_cache = list(rows[0].keys())
        else:
            # mid-file read — header row is not in the chunk; use cached copy
            # or fall back to reading it from disk (only on first mid-file poll)
            if self._header_cache is None:
                self._header_cache = self._read_header(path)
            if self._header_cache is None:
                log.warning("ObsFileWatcher: could not read header from %s", path.name)
                return
            rows = list(csv.DictReader(io.StringIO(text), fieldnames=self._header_cache))

        parsed = 0
        for row in rows:
            obs = self._parse_row(row)
            if obs is not None:
                self.obs_ready.emit(obs)
                parsed += 1

        if parsed:
            self._last_pos = new_pos
            log.debug("ObsFileWatcher: emitted %d obs from %s", parsed, path.name)

    @staticmethod
    def _read_header(path: Path) -> list[str] | None:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                return next(csv.reader([fh.readline()]))
        except Exception:
            return None

    def _parse_row(self, row: dict) -> Optional[Observation]:
        f = self._fields
        try:
            lat = float(row[f.lat])
            lon = float(row[f.lon])
        except (KeyError, ValueError, TypeError):
            return None

        ts = self._parse_timestamp(row)

        return Observation(
            vehicle_id=self._vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=ts,
            temperature_c=_float_or_none(row.get(f.temperature_c)),
            dewpoint_c=_float_or_none(row.get(f.dewpoint_c)),
            wind_speed_ms=_float_or_none(row.get(f.wind_speed_ms)),
            wind_dir_deg=_float_or_none(row.get(f.wind_dir_deg)),
            pressure_mb=_float_or_none(row.get(f.pressure_mb)),
        )

    def _parse_timestamp(self, row: dict) -> datetime:
        f = self._fields

        # single combined column takes priority
        if f.timestamp_col:
            return _parse_iso_timestamp(row.get(f.timestamp_col, ""))

        # split date (DDMMYY) + time (HHMMSS) columns
        date_str = (row.get(f.date_col) or "").strip()
        time_str = (row.get(f.time_col) or "").strip()

        if date_str and time_str:
            combined = date_str + time_str   # e.g. "050625" + "175228" → "050625175228"
            try:
                return datetime.strptime(combined, "%d%m%y%H%M%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        return datetime.now(timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_iso_timestamp(raw: str | None) -> datetime:
    text = (raw or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _float_or_none(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
