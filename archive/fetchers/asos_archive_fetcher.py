"""ASOS / METAR history for ARCHIVE, replayed on the archive clock, ported
from the storm_ty_edits fork.

The user draws a box on the map (the same box as live ASOS); every roster
station inside it (data/fetchers/surface_fetcher.load_asos_station_roster)
is fetched once for the whole session from IEM's history service:

    https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py

``station=`` repeats, so one request covers a batch of stations. IEM allows
about one request per second per address, so batches go one after another.
The clock then picks each station's latest report at or before it (bisect),
as ArchiveVehicleObsFetcher does for vehicles. Drawing a new box replaces
the stations; a load still running for the old box is dropped.
"""
from __future__ import annotations

import bisect
import csv
import io
import logging
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

import config
from archive.session import session_bounds
from core import package_sources
from core.observation import Observation
from core.qt_signals import emit_alive
from data import endpoints
from data.fetchers.surface_fetcher import MAX_ASOS_STATIONS, load_asos_station_roster

log = logging.getLogger(__name__)

_ASOS_HISTORY_URL = endpoints.IEM_ASOS_HISTORY
_REQUEST_FIELDS = ("tmpf", "dwpf", "sknt", "drct", "alti", "mslp")
# fewer than live's 100 per request (IEM_CURRENTS_BATCH): each station
# brings a whole session of reports, not one
_BATCH_SIZE = 50
_BATCH_PACE_S = 1.0  # IEM's one request per second
_REQUEST_TIMEOUT = 30
_RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF_S = (2.0, 5.0)


def _urlopen_with_retry(request: Request):
    attempts = len(_RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT)
        except HTTPError as exc:
            if exc.code not in _RETRYABLE_HTTP_CODES or attempt == attempts - 1:
                raise
        except URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(_RETRY_BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def stations_in_bbox(roster: dict[str, dict], west: float, south: float, east: float, north: float) -> list[str]:
    return sorted(
        stid for stid, meta in roster.items()
        if south <= meta["lat"] <= north and west <= meta["lon"] <= east
    )


def _batch_url(station_ids: list[str], sts: datetime, ets: datetime) -> str:
    parts = [f"data={f}" for f in _REQUEST_FIELDS]
    parts += [f"station={s}" for s in station_ids]
    parts += [
        f"sts={sts.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"ets={ets.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "tz=UTC",
        "format=onlycomma",
    ]
    return f"{_ASOS_HISTORY_URL}?{'&'.join(parts)}"


def _float_or_none(value: str | None) -> float | None:
    if value is None or value in ("", "M"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_valid(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_asos_history_csv(raw_text: str, roster: dict[str, dict]) -> dict[str, list[Observation]]:
    """One onlycomma CSV response as time-sorted Observations per station.
    Units as live ASOS (SurfaceFetcher._fetch_iem_batch): °F to °C, knots to
    m/s, sea-level pressure in mb (MSLP, else the altimeter setting). Rows
    for stations not in the roster (no position) are dropped."""
    result: dict[str, list[Observation]] = {}
    for row in csv.DictReader(io.StringIO(raw_text)):
        stid = (row.get("station") or "").strip().upper()
        meta = roster.get(stid)
        if not stid or meta is None:
            continue
        ts = _parse_valid(row.get("valid"))
        if ts is None:
            continue

        tmpf = _float_or_none(row.get("tmpf"))
        dwpf = _float_or_none(row.get("dwpf"))
        sknt = _float_or_none(row.get("sknt"))
        mslp = _float_or_none(row.get("mslp"))
        pres = mslp if (mslp is not None and 870.0 <= mslp <= 1090.0) else None
        if pres is None:
            alti = _float_or_none(row.get("alti"))
            if alti is not None and 27.0 <= alti <= 32.0:
                pres = alti * 33.8639

        result.setdefault(stid, []).append(Observation(
            vehicle_id=f"surface:asos:{stid}",
            lat=meta["lat"],
            lon=meta["lon"],
            timestamp=ts,
            icon_type="mesonet",
            temperature_c=(tmpf - 32.0) * 5.0 / 9.0 if tmpf is not None else None,
            dewpoint_c=(dwpf - 32.0) * 5.0 / 9.0 if dwpf is not None else None,
            wind_speed_ms=sknt * 0.514444 if sknt is not None else None,
            wind_dir_deg=_float_or_none(row.get("drct")),
            pressure_mb=pres,
        ))

    for obs_list in result.values():
        obs_list.sort(key=lambda o: o.timestamp)
    return result


def fetch_asos_history(
    station_ids: list[str], roster: dict[str, dict], day: datetime, *,
    cancel: threading.Event | None = None,
) -> dict[str, list[Observation]]:
    """ASOS history for the stations over the session's span (its UTC day
    and the next morning, archive/session.py), in paced batches. `cancel` is
    checked between batches."""
    sts, ets = session_bounds(day)
    merged: dict[str, list[Observation]] = {}
    batches = [station_ids[i:i + _BATCH_SIZE] for i in range(0, len(station_ids), _BATCH_SIZE)]
    for i, batch in enumerate(batches):
        if cancel is not None and cancel.is_set():
            return merged
        request = Request(_batch_url(batch, sts, ets), headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
        raw_text = package_sources.read_url("asos", request, _urlopen_with_retry).decode("utf-8", errors="replace")
        for stid, obs_list in parse_asos_history_csv(raw_text, roster).items():
            merged.setdefault(stid, []).extend(obs_list)
        if i < len(batches) - 1:
            time.sleep(_BATCH_PACE_S)

    for obs_list in merged.values():
        obs_list.sort(key=lambda o: o.timestamp)
    return merged


class ArchiveAsosFetcher(QObject):
    """Loads the ASOS history for a drawn box and replays it on the clock."""

    station_updated = pyqtSignal(str, str, object)  # station id, name, Observation
    station_removed = pyqtSignal(str)               # no report yet at the clock
    stations_cleared = pyqtSignal()
    load_started = pyqtSignal(int)                  # stations being fetched
    load_finished = pyqtSignal(int)                 # stations with reports
    error = pyqtSignal(str)
    # worker -> UI thread: (generation, observations by station, names)
    _results = pyqtSignal(int, object, object)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._session_date = session_date
        self._observations: dict[str, list[Observation]] = {}
        self._timestamps: dict[str, list[datetime]] = {}
        self._names: dict[str, str] = {}
        self._shown: dict[str, int] = {}      # station -> index on the map
        self._time: datetime | None = None
        self._generation = 0
        self._cancel: threading.Event | None = None
        self._loading = False
        self._results.connect(self._on_results)

    # ── state ────────────────────────────────────────────────
    def has_stations(self) -> bool:
        return bool(self._observations)

    def is_loading(self) -> bool:
        return self._loading

    def observations_by_station(self) -> dict[str, list[Observation]]:
        """Each station's whole session of reports, time-ordered."""
        return dict(self._observations)

    # ── loading ──────────────────────────────────────────────
    def set_bbox(self, west: float, south: float, east: float, north: float) -> None:
        """Replace the stations with those inside this box."""
        self.clear()
        self._loading = True
        threading.Thread(
            target=self._load, args=(self._generation, west, south, east, north, self._cancel),
            daemon=True, name="asos-history",
        ).start()

    def clear(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        self._cancel = threading.Event()
        self._generation += 1
        self._loading = False
        had = bool(self._observations) or bool(self._shown)
        self._observations, self._timestamps, self._names, self._shown = {}, {}, {}, {}
        if had:
            self.stations_cleared.emit()

    def _load(self, generation: int, west: float, south: float, east: float, north: float,
              cancel: threading.Event) -> None:
        try:
            roster = load_asos_station_roster()
        except Exception as exc:  # noqa: BLE001
            log.warning("Archive ASOS: station roster unavailable: %s", exc)
            if generation == self._generation:
                emit_alive(self, "error", f"ASOS station list unavailable: {exc}")
            return

        station_ids = stations_in_bbox(roster, west, south, east, north)
        if len(station_ids) > MAX_ASOS_STATIONS:
            log.warning("Archive ASOS: %d stations in the box; keeping %d",
                        len(station_ids), MAX_ASOS_STATIONS)
            station_ids = station_ids[:MAX_ASOS_STATIONS]
        if station_ids and generation == self._generation:
            emit_alive(self, "load_started", len(station_ids))

        by_station: dict[str, list[Observation]] = {}
        if station_ids:
            try:
                by_station = fetch_asos_history(station_ids, roster, self._session_date, cancel=cancel)
            except Exception as exc:  # noqa: BLE001
                log.warning("Archive ASOS: history fetch failed: %s", exc)
                if generation == self._generation:
                    emit_alive(self, "error", f"ASOS history unavailable: {exc}")
                return
        if cancel.is_set():
            return
        names = {stid: roster.get(stid, {}).get("name", stid) for stid in by_station}
        emit_alive(self, "_results", generation, by_station, names)

    def _on_results(self, generation: int, by_station: dict, names: dict) -> None:
        if generation != self._generation:
            return                      # a newer box was drawn meanwhile
        self._loading = False
        self._observations = by_station
        self._timestamps = {stid: [o.timestamp for o in obs] for stid, obs in by_station.items()}
        self._names = names
        self._shown = {}
        self.load_finished.emit(len(by_station))
        if self._time is not None:
            self.on_time_changed(self._time)

    # ── replay ───────────────────────────────────────────────
    def on_time_changed(self, archive_time: datetime) -> None:
        self._time = archive_time
        for station_id, timestamps in self._timestamps.items():
            index = bisect.bisect_right(timestamps, archive_time) - 1
            if self._shown.get(station_id, -1) == index:
                continue
            if index < 0:
                del self._shown[station_id]
                self.station_removed.emit(station_id)
                continue
            self._shown[station_id] = index
            obs = self._observations[station_id][index]
            self.station_updated.emit(station_id, self._names.get(station_id, station_id), obs)
