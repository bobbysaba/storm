import csv
import io
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Callable, Optional

from .obs_columns import FieldMap, row_time
from .observation import Observation

__all__ = ["FieldMap", "ObsFileTail"]

log = logging.getLogger(__name__)


class ObsFileTail:
    """
    Incrementally reads today's file inside a directory, picked by the
    layout's ``file`` pattern.  Each call to poll() parses any complete rows
    appended since the last call and returns the latest one.  Rolls over to
    the next day's file automatically at midnight UTC.

    No Qt and no threads: callers decide how often to poll.
    """

    def __init__(self, data_dir: str,
                 vehicle_id: str,
                 field_map: FieldMap | None = None,
                 now_fn: Callable[[], datetime] | None = None):
        self._data_dir   = Path(data_dir)
        self._vehicle_id = vehicle_id
        self._fields     = field_map or FieldMap()
        self._now        = now_fn or (lambda: datetime.now(timezone.utc))

        self._current_date: date | None = None
        self._current_path: Path | None = None
        self._last_mtime: float = 0.0
        self._last_size:  int   = 0
        self._last_pos:   int   = 0
        # cached header columns — read once per file, avoids a separate open()
        self._header_cache: list[str] | None = None
        # the file whose header was found to lack the position columns (warned once)
        self._warned_header: Path | None = None

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def current_path(self) -> Path | None:
        return self._current_path


    def today_path(self) -> Optional[Path]:
        """Return the file to watch, or None when there's none yet.

        A pattern with {date}: the matching file for today's UTC date,
        shortest name first (a bare YYYYMMDD.txt before GPS_x_YYYYMMDD.txt).

        Without {date}: the most recently modified matching file, since the
        logger keeps appending to the file it opened at session start, which
        may be yesterday's after midnight.
        """
        if not self._fields.daily:
            candidates = list(self._data_dir.glob(self._fields.file))
            if not candidates:
                return None
            return max(candidates, key=lambda p: p.stat().st_mtime)

        matches = list(self._data_dir.glob(self._fields.file_glob(self._now())))
        if not matches:
            return None   # the watcher waits for it
        return min(matches, key=lambda p: (len(p.name), p.name))

    def _roll_to_today(self):
        """Switch to the active file if it has changed, resetting byte position."""
        if self._fields.daily:
            today = self._now().date()
            if today == self._current_date and self._current_path is not None:
                return
            self._current_date = today
        new_path = self.today_path()
        if new_path == self._current_path:
            return
        self._current_path  = new_path
        self._last_mtime    = 0.0
        self._last_size     = 0
        self._last_pos      = 0
        self._header_cache  = None
        log.info("ObsFileTail: active file → %s", self._current_path)

    def poll(self) -> Observation | None:
        """Return the latest newly appended observation, or None."""
        # check for date rollover first
        self._roll_to_today()

        path = self._current_path
        if path is None or not path.exists():
            return None

        try:
            stat = path.stat()
        except OSError:
            return None

        mtime = stat.st_mtime
        size  = stat.st_size

        if mtime == self._last_mtime:
            return None   # nothing changed

        self._last_mtime = mtime

        # file shrank → it was rewritten; restart from top
        if size < self._last_size:
            log.debug("ObsFileTail: file shrank, resetting position")
            self._last_pos = 0

        self._last_size = size

        try:
            with path.open("rb") as fh:
                fh.seek(self._last_pos)
                chunk = fh.read()
        except OSError as e:
            log.warning("ObsFileTail: read error: %s", e)
            return None

        if not chunk:
            return None

        # guard against a partially-written trailing row.  The logger writes
        last_nl = chunk.rfind(b'\n')
        if last_nl == -1:
            # no complete line in the new bytes yet; wait for next poll.
            return None
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
                log.warning("ObsFileTail: could not read header from %s", path.name)
                return None
            rows = list(csv.DictReader(io.StringIO(text), fieldnames=self._header_cache))

        self._check_header(path)
        parsed = 0
        last_obs: Observation | None = None
        for row in rows:
            obs = self._parse_row(row)
            if obs is not None:
                parsed += 1
                last_obs = obs

        if parsed and last_obs is not None:
            self._last_pos = new_pos
            log.debug(
                "ObsFileTail: parsed %d obs, latest from %s",
                parsed,
                path.name,
            )
            return last_obs
        return None

    def _check_header(self, path: Path) -> None:
        """Warn once per file when its header has no position columns, the
        usual reason a logger's vehicle never shows up ([obs_columns])."""
        header = self._header_cache
        if header is None or self._warned_header == path:
            return
        missing = [c for c in (self._fields.lat, self._fields.lon) if c not in header]
        if missing:
            self._warned_header = path
            log.warning("ObsFileTail: %s has no %s column (its first line: %s); "
                        "check [obs_columns], or the file's header row",
                        path.name, " or ".join(repr(c) for c in missing),
                        ",".join(header)[:120])

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
            temperature_c=f.to_storm("temperature_c", float_or_none(row.get(f.temperature_c))),
            dewpoint_c=f.to_storm("dewpoint_c", float_or_none(row.get(f.dewpoint_c))),
            wind_speed_ms=f.to_storm("wind_speed_ms", float_or_none(row.get(f.wind_speed_ms))),
            wind_dir_deg=float_or_none(row.get(f.wind_dir_deg)),
            pressure_mb=f.to_storm("pressure_mb", float_or_none(row.get(f.pressure_mb))),
            heading_deg=float_or_none(row.get(f.heading_deg)),
        )

    def _parse_timestamp(self, row: dict) -> datetime:
        # a row without a readable time is taken as just logged
        return row_time(row, self._fields) or datetime.now(timezone.utc)


def float_or_none(value) -> float | None:
    """*value* as a float, or None when it's None, blank or not a number."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
