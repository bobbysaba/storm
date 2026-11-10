"""Compact per-vehicle observation history with optional per-day SQLite persistence.

Each vehicle keeps its observations as growing numeric columns (about 40 bytes
per obs) instead of a list of Observation objects. Observation objects are
built on demand for the few callers that need them.
"""

from __future__ import annotations

import bisect
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from core.observation import Observation

log = logging.getLogger(__name__)

# met fields stored per obs, in column order
FIELDS = (
    "lat", "lon", "temperature_c", "dewpoint_c",
    "wind_speed_ms", "wind_dir_deg", "pressure_mb",
)
_INITIAL_CAPACITY = 256
_KEEP_DAYS = 2
_COMMIT_INTERVAL_S = 1.0
_FILE_PREFIX = "vehicle_history_"


class VehicleHistory:
    """Time-sorted observation columns for one vehicle."""

    def __init__(self, vehicle_id: str):
        self.vehicle_id = vehicle_id
        self._n = 0
        self._times = np.empty(_INITIAL_CAPACITY, dtype=np.float64)
        self._values = np.empty((_INITIAL_CAPACITY, len(FIELDS)), dtype=np.float32)

    def __len__(self) -> int:
        return self._n

    def __bool__(self) -> bool:
        return self._n > 0

    def __iter__(self):
        for i in range(self._n):
            yield self.observation(i)

    def __getitem__(self, index: int) -> Observation:
        if index < 0:
            index += self._n
        if not 0 <= index < self._n:
            raise IndexError(index)
        return self.observation(index)

    @property
    def times(self) -> np.ndarray:
        """Epoch seconds (UTC), sorted ascending."""
        return self._times[:self._n]

    def column(self, field: str) -> np.ndarray:
        """One met column as float32 with NaN for missing values."""
        return self._values[:self._n, FIELDS.index(field)]

    def append(self, obs: Observation) -> bool:
        """Add one observation; returns False for a duplicate timestamp."""
        t = obs.timestamp.timestamp()
        row = [np.nan if (v := getattr(obs, f)) is None else v for f in FIELDS]
        return self.append_row(t, row)

    def append_row(self, t: float, row) -> bool:
        times = self.times
        if self._n and t <= times[-1]:
            # out of order is rare; keep the columns sorted
            pos = int(np.searchsorted(times, t))
            if pos < self._n and times[pos] == t:
                return False
        else:
            pos = self._n
        if self._n == len(self._times):
            self._grow()
        if pos < self._n:
            self._times[pos + 1:self._n + 1] = self._times[pos:self._n]
            self._values[pos + 1:self._n + 1] = self._values[pos:self._n]
        self._times[pos] = t
        self._values[pos] = row
        self._n += 1
        return True

    def _grow(self) -> None:
        cap = len(self._times) * 2
        times = np.empty(cap, dtype=np.float64)
        values = np.empty((cap, len(FIELDS)), dtype=np.float32)
        times[:self._n] = self._times[:self._n]
        values[:self._n] = self._values[:self._n]
        self._times, self._values = times, values

    def observation(self, index: int) -> Observation:
        row = self._values[index]
        kwargs = {
            f: (None if np.isnan(v) else float(v)) for f, v in zip(FIELDS, row)
        }
        return Observation(
            vehicle_id=self.vehicle_id,
            timestamp=datetime.fromtimestamp(float(self._times[index]), timezone.utc),
            **kwargs,
        )

    def recent(self, seconds: float, now: datetime) -> list[Observation]:
        """Observations in (now - seconds, now], plus the one just before.

        The extra leading obs lets a window calculation find its start point.
        """
        times = self.times
        end = bisect.bisect_right(times, now.timestamp())
        start = max(bisect.bisect_right(times, now.timestamp() - seconds) - 1, 0)
        return [self.observation(i) for i in range(start, end)]


class VehicleHistoryStore:
    """All vehicles' histories, appended to a per-day SQLite file when persisting."""

    def __init__(self, data_dir: str | Path | None = None):
        self._histories: dict[str, VehicleHistory] = {}
        self._dir = Path(data_dir) if data_dir else None
        self._conn: sqlite3.Connection | None = None
        self._conn_day: date | None = None
        self._last_commit = 0.0
        if self._dir is not None:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._prune()
                self._load_day(datetime.now(timezone.utc).date())
            except (OSError, sqlite3.Error):
                log.exception("Vehicle history persistence disabled")
                self._dir = None

    def __contains__(self, vehicle_id: str) -> bool:
        return vehicle_id in self._histories

    def get(self, vehicle_id: str) -> VehicleHistory | None:
        return self._histories.get(vehicle_id)

    def items(self):
        return self._histories.items()

    def append(self, obs: Observation) -> VehicleHistory:
        hist = self._histories.get(obs.vehicle_id)
        if hist is None:
            hist = self._histories[obs.vehicle_id] = VehicleHistory(obs.vehicle_id)
        if hist.append(obs) and self._dir is not None:
            self._persist(obs)
        return hist

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                log.exception("Closing vehicle history file failed")
            self._conn = None

    # persistence

    def _path(self, day: date) -> Path:
        return self._dir / f"{_FILE_PREFIX}{day.isoformat()}.sqlite"

    def _prune(self) -> None:
        oldest = datetime.now(timezone.utc).date() - timedelta(days=_KEEP_DAYS - 1)
        for path in self._dir.glob(f"{_FILE_PREFIX}*.sqlite*"):
            stem = path.name[len(_FILE_PREFIX):].split(".")[0]
            try:
                day = date.fromisoformat(stem)
            except ValueError:
                continue
            if day < oldest:
                path.unlink(missing_ok=True)

    def _load_day(self, day: date) -> None:
        path = self._path(day)
        if not path.exists():
            return
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                f"SELECT vehicle_id, t, {', '.join(FIELDS)} FROM obs ORDER BY t"
            ).fetchall()
        finally:
            conn.close()
        for vid, t, *vals in rows:
            hist = self._histories.get(vid)
            if hist is None:
                hist = self._histories[vid] = VehicleHistory(vid)
            hist.append_row(t, [np.nan if v is None else v for v in vals])
        log.info("Loaded %d vehicle observations from %s", len(rows), path.name)

    def _connection(self, day: date) -> sqlite3.Connection:
        if self._conn is None or self._conn_day != day:
            self.close()
            conn = sqlite3.connect(self._path(day))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS obs (vehicle_id TEXT NOT NULL, "
                f"t REAL NOT NULL, {', '.join(f + ' REAL' for f in FIELDS)})"
            )
            self._conn, self._conn_day = conn, day
        return self._conn

    def _persist(self, obs: Observation) -> None:
        try:
            conn = self._connection(obs.timestamp.astimezone(timezone.utc).date())
            conn.execute(
                f"INSERT INTO obs VALUES (?, ?, {', '.join('?' for _ in FIELDS)})",
                (obs.vehicle_id, obs.timestamp.timestamp(),
                 *(getattr(obs, f) for f in FIELDS)),
            )
            now = time.monotonic()
            if now - self._last_commit >= _COMMIT_INTERVAL_S:
                conn.commit()
                self._last_commit = now
        except sqlite3.Error:
            log.exception("Writing vehicle history failed")
