
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
    """
    lat:           str = "lat"
    lon:           str = "lon"
    # split date + time columns (matches FOFS truck logger)
    date_col:      str = "gps_date"    # DDMMYY  e.g. "050625"
    time_col:      str = "gps_time"    # HHMMSS  e.g. "175228"
    # met fields
    temperature_c: str = "t_fast"
    dewpoint_c:    str = "dewpoint"
    wind_speed_ms: str = "sfc_wspd"
    wind_dir_deg:  str = "sfc_wdir"
    pressure_mb:   str = "pressure"


class ObsFileWatcher(QObject):
    """
    Watches today's *YYYYMMDD.txt inside a directory for new rows and
    emits obs_ready for each one.  Rolls over to the next day's file
    automatically at midnight.

    When gps_mode=True the GPS Ka FieldMap is expected (Longitude/Latitude
    columns, no met fields).  When False the FOFS FieldMap is used.

    If both a bare YYYYMMDD.txt and a prefixed GPS_*YYYYMMDD.txt exist,
    the file matching the current gps_mode is preferred.
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, data_dir: str,
                 vehicle_id: str,
                 field_map: FieldMap | None = None,
                 poll_interval_s: int = 10,
                 gps_mode: bool = False,
                 parent=None):
        super().__init__(parent)
        self._data_dir   = Path(data_dir)
        self._vehicle_id = vehicle_id
        self._fields     = field_map or FieldMap()
        self._poll_ms    = poll_interval_s * 1000
        self._gps_mode   = gps_mode

        self._current_date: date | None = None
        self._current_path: Path | None = None
        self._last_mtime: float = 0.0
        self._last_size:  int   = 0
        self._last_pos:   int   = 0
        # cached header columns — read once per file, avoids a separate open()
        self._header_cache: list[str] | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)


    def start(self):
        self._roll_to_today()
        self._poll()
        self._timer.start(self._poll_ms)
        log.info("ObsFileWatcher: watching %s every %ds",
                 self._data_dir, self._poll_ms // 1000)

    def stop(self):
        self._timer.stop()


    def _today_path(self) -> Optional[Path]:
        """Return the file to watch.

        FOFS mode: construct YYYYMMDD.txt from today's UTC date, with
        multi-match preference for the bare (unprefixed) file.

        GPS mode: pick the most recently modified GPS_*_????????.txt in the
        directory — the logger keeps appending to the file it opened at
        session start, which may be yesterday's date after midnight rollover.
        """
        if self._gps_mode:
            candidates = list(self._data_dir.glob("*_????????.txt"))
            if not candidates:
                return None
            return max(candidates, key=lambda p: p.stat().st_mtime)

        date_suffix = datetime.now(timezone.utc).strftime("%Y%m%d.txt")
        matches = sorted(self._data_dir.glob(f"*{date_suffix}"))
        if not matches:
            return self._data_dir / date_suffix  # non-existent; watcher will wait
        if len(matches) == 1:
            return matches[0]
        # multiple matches — prefer bare YYYYMMDD.txt for FOFS
        exact = self._data_dir / date_suffix
        return exact if exact in matches else matches[0]

    def _roll_to_today(self):
        """Switch to the active file if it has changed, resetting byte position."""
        if self._gps_mode:
            # re-glob for the newest GPS file; switch if a different one appears
            new_path = self._today_path()
            if new_path != self._current_path:
                self._current_path  = new_path
                self._last_mtime    = 0.0
                self._last_size     = 0
                self._last_pos      = 0
                self._header_cache  = None
                log.info("ObsFileWatcher: active GPS file → %s", self._current_path)
            return

        today = datetime.now(timezone.utc).date()
        if today == self._current_date:
            return
        self._current_date  = today
        self._current_path  = self._today_path()
        self._last_mtime    = 0.0
        self._last_size     = 0
        self._last_pos      = 0
        self._header_cache  = None
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

        # guard against a partially-written trailing row.  The logger writes
        last_nl = chunk.rfind(b'\n')
        if last_nl == -1:
            # no complete line in the new bytes yet; wait for next poll.
            return
        chunk = chunk[:last_nl + 1]
        new_pos = self._last_pos + last_nl + 1

        text = chunk.decode("utf-8", errors="replace")

        if self._last_pos == 0:
            # reading from the top — header row is present in the chunk;
            rows = list(csv.DictReader(io.StringIO(text)))
            # also warm the header cache so mid-file polls don't need a disk read
            if rows:
                self._header_cache = list(rows[0].keys())
        else:
            # mid-file read — header row is not in the chunk; use cached copy
            if self._header_cache is None:
                self._header_cache = self._read_header(path)
            if self._header_cache is None:
                log.warning("ObsFileWatcher: could not read header from %s", path.name)
                return
            rows = list(csv.DictReader(io.StringIO(text), fieldnames=self._header_cache))

        parsed = 0
        last_obs: Observation | None = None
        for row in rows:
            obs = self._parse_row(row)
            if obs is not None:
                parsed += 1
                last_obs = obs

        if parsed and last_obs is not None:
            self.obs_ready.emit(last_obs)
            self._last_pos = new_pos
            log.debug(
                "ObsFileWatcher: parsed %d obs, emitted latest from %s",
                parsed,
                path.name,
            )

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
