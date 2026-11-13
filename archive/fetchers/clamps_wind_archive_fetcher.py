"""Doppler-lidar wind profiles (VAD and CSM winds) from the lidar truck and the
CLAMPS trailers, for the archive VAD window.

The FRDD/CLAMPS products map onto core/vad.py's VADProfile / VADSet, so the
VAD window shows them unchanged (hodograph and time-height). Two kinds carry
the same core fields (height, speed, direction, a fit-quality RMS):

  - "vad"      -- classic VAD retrievals, roughly every 10-60 minutes
  - "csmwinds" -- winds from continuous-scan mode, near-continuous

Each source is one instrument's datastream; every date is checked against
all of them. A date with no file is the usual case, not an error. Files are
found by listing the folder (one cached listing per stream), not by guessing
names.
"""

from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, pyqtSignal

from archive.fetchers import clamps_common
from archive.session import session_bounds, session_days
from core.qt_signals import emit_alive
from core.vad import VADProfile, VADSet
from data import thredds_paths as paths

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 120   # these files run tens of MB to about 100 MB

# Generous physical bounds: wide enough never to reject a real measurement,
# tight enough to reject the fill values seen in real files (wspd's repeated
# ~2745 kt, rms's float32 default fill of ~1.9e37 kt after conversion).
_MAX_PLAUSIBLE_WSPD_KT = 250.0
_MAX_PLAUSIBLE_RMS_KT = 500.0
_MIN_CSM_INTENSITY = 1.01      # SNR + 1; -20 dB
_MAX_CSM_SIGMA_MS = 2.0


@dataclass(frozen=True)
class ClampsWindSource:
    """One instrument datastream that produces wind profiles."""
    platform_id: str     # STORM id, e.g. "CLAMPS1-VAD"
    platform_dir: str    # under FRDD/CLAMPS, e.g. "clamps/clamps1"
    datastream: str      # e.g. "clampsdlvadC1.c1", also the file prefix


KNOWN_CLAMPS_WIND_SOURCES: tuple[ClampsWindSource, ...] = tuple(
    ClampsWindSource(*row) for row in paths.CLAMPS_WIND_STREAMS)


def wind_source_name(platform_id: str) -> str:
    """What to call a source: the lidar truck's are not CLAMPS trailer data,
    though they come through the same CLAMPS processing."""
    instrument, _, rest = platform_id.partition("-")
    kind = {"VAD": "VAD", "CSMWINDS": "CSM winds"}.get(rest.split("-")[-1], rest)
    if instrument == "DLTRUCK1":
        return f"Lidar truck {kind} ({rest.split('-')[0]})"
    return f"{instrument} trailer {kind}"


def parse_clamps_wind_netcdf(data: bytes, platform_id: str) -> list[VADProfile]:
    """Parse a CLAMPS dlvad / dlcsmwinds file into VADProfiles.

    height is km AGL -> m; wspd and rms are m/s -> kt (VADProfile's unit).
    rms is per (time, height) in the file and is reduced to one value per
    profile (the mean over valid heights) for VADProfile.rms_error.

    None of these variables declare a fill value, and bad cells differ
    between files and fields (found on real files, in the fork):
      - a CLAMPS1 VAD file: a repeated finite wspd (1412.8 "m/s") and rms
        (-999) lining up with a large negative r_sq;
      - a lidar truck CSM file: rms entirely the netCDF default fill
        (9.97e36), and about 5 % of wspd at 1e9-1e10 with a plausible r_sq;
      - a 2026-05-17 truck VAD file: -999 fills and, separately, r_sq in
        (-100, 0) at ~126 m/s, just under the speed bound.
    So r_sq >= 0 (a negative r_sq is a failed fit) masks cells when present,
    isfinite() backs it up, and speed and rms each get a physical bound.

    CSM-winds files need more: on the lidar truck's DL2 file for 2022-05-19,
    38 % of the cells passing the above were over 100 kt, fitted with r_sq
    near 1, at heights up to 11 km: fits to noise above the aerosol layer.
    Those files carry intensity (SNR + 1) and wspd_sigma; cells need an
    intensity of at least 1.01 (SNR -20 dB, the usual Halo lidar cutoff) and
    a speed uncertainty of at most 2 m/s, which left 0.6 % over 100 kt.
    VAD files have no wspd_sigma and aren't filtered on intensity: a VAD
    averages a whole scan, and CLAMPS2's good VAD winds sit at 1.002.
    """
    import numpy as np
    import xarray as xr

    from core.vad import MS_TO_KNOT

    # decode_times=False: base_time + time_offset are combined by hand
    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        height_m = np.asarray(ds["height"].values, dtype="float64") * 1000.0
        wspd_kt = np.asarray(ds["wspd"].values, dtype="float64") * MS_TO_KNOT
        wdir = np.asarray(ds["wdir"].values, dtype="float64")
        rms_kt = np.asarray(ds["rms"].values, dtype="float64") * MS_TO_KNOT if "rms" in ds else None
        r_sq = np.asarray(ds["r_sq"].values, dtype="float64") if "r_sq" in ds else None
        csm = "wspd_sigma" in ds and "intensity" in ds
        if csm:
            intensity = np.asarray(ds["intensity"].values, dtype="float64")
            wspd_sigma = np.asarray(ds["wspd_sigma"].values, dtype="float64")

    profiles: list[VADProfile] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        if not np.isfinite(t_off):
            continue
        spd_row = wspd_kt[i]
        dir_row = wdir[i]
        with np.errstate(invalid="ignore"):
            valid = (
                np.isfinite(spd_row) & (np.abs(spd_row) < _MAX_PLAUSIBLE_WSPD_KT)
                & np.isfinite(dir_row) & np.isfinite(height_m)
            )
            if r_sq is not None:
                valid &= np.isfinite(r_sq[i]) & (r_sq[i] >= 0.0)
            if csm:
                valid &= (intensity[i] >= _MIN_CSM_INTENSITY) & (wspd_sigma[i] <= _MAX_CSM_SIGMA_MS)
        if not np.any(valid):
            continue

        rms_error = 0.0
        if rms_kt is not None:
            row_rms = rms_kt[i][valid]
            with np.errstate(invalid="ignore"):
                row_rms = row_rms[np.isfinite(row_rms) & (np.abs(row_rms) < _MAX_PLAUSIBLE_RMS_KT)]
            if row_rms.size:
                rms_error = float(np.mean(row_rms))

        profiles.append(VADProfile(
            timestamp=datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc),
            site=platform_id,
            heights_m=height_m[valid],
            wind_dir=dir_row[valid],
            wind_spd=spd_row[valid],
            rms_error=rms_error,
        ))
    return profiles


def _files_for_day(source: ClampsWindSource, day: datetime) -> list[str]:
    folder = paths.clamps_processed(source.platform_dir, source.datastream)
    prefix = f"{source.datastream}.{day:%Y%m%d}."
    return [paths.file_url(f"{folder}/{name}")
            for name in sorted(clamps_common.list_folder(folder))
            if name.startswith(prefix) and name.endswith((".cdf", ".nc"))]


def fetch_clamps_wind_sets(session_time: datetime) -> tuple[dict[str, VADSet], list[str]]:
    """Every source's profiles over the session span (its UTC day and the
    next morning to the cap; CLAMPS runs continuously). Returns the sets by
    platform id and the failures (a listing or download that didn't answer)."""
    start, cap = session_bounds(session_time)
    results: dict[str, VADSet] = {}
    errors: list[str] = []
    for source in KNOWN_CLAMPS_WIND_SOURCES:
        profiles: list[VADProfile] = []
        for day in session_days(session_time):
            try:
                urls = _files_for_day(source, day)
            except Exception as exc:  # noqa: BLE001 - listing failed; try the rest
                errors.append(f"{source.platform_id}: {exc}")
                break
            for url in urls:
                try:
                    data = clamps_common.read("clamps profiles", url, timeout=_REQUEST_TIMEOUT)
                    profiles += parse_clamps_wind_netcdf(data, source.platform_id)
                except Exception as exc:  # noqa: BLE001 - network or unexpected schema
                    log.warning("CLAMPS wind %s: %s failed: %s", source.platform_id, url, exc)
                    errors.append(f"{source.platform_id}: {exc}")
        profiles = [p for p in profiles if start <= p.timestamp <= cap]
        if profiles:
            unique = {p.timestamp: p for p in profiles}    # a restart can overlap
            results[source.platform_id] = VADSet(profiles=list(unique.values()))
    return results, errors


class ArchiveClampsWindFetcher(QObject):
    """Loads every wind-profile source for the session, once, in the
    background. The sets are kept, so opening the VAD window again
    doesn't download them again."""

    sets_ready = pyqtSignal(dict)   # {platform_id: VADSet}
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._session_date = session_date
        self._lock = threading.Lock()
        self._busy = False
        self._sets: dict[str, VADSet] | None = None

    @property
    def sets(self) -> dict[str, VADSet] | None:
        """The loaded sets, or None before the first load has finished."""
        return self._sets

    def is_loading(self) -> bool:
        return self._busy

    def fetch(self) -> bool:
        """Load the sets (or re-emit them when they're in). False when a load
        is already running: its sets_ready will come."""
        if self._sets is not None:
            self.sets_ready.emit(dict(self._sets))
            return True
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._bg_fetch, daemon=True, name="clamps-wind").start()
        return True

    def _bg_fetch(self) -> None:
        try:
            results, errors = fetch_clamps_wind_sets(self._session_date)
        except Exception as exc:  # noqa: BLE001
            results, errors = {}, [str(exc)]
        with self._lock:
            self._busy = False
            if results or not errors:
                self._sets = results     # a failed load is asked again next time
        if errors and not results:
            emit_alive(self, "error", "; ".join(dict.fromkeys(errors)))
            return
        emit_alive(self, "sets_ready", results)
