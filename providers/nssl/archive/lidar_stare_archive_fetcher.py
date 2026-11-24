"""Fixed-point (vertical) stares from the lidar truck's and the CLAMPS
trailers' Doppler lidars, for the stares window (ui/dialogs/lidar_stare_dialog.py):
each lidar's "fp" files for the session, read as time x height.

The files (FRDD/CLAMPS/<platform>/ingested/<prefix>dlfp<unit>.b1, one per
UTC day) hold every vertical ray the lidar took, in time order: long stares
on days it was set to stare, and on CSM days one ray between conical scans.
Their axis is height (km AGL), not range, and they carry a cloud-base
height (`cbh`, -1 for none) worked out from the backscatter.

Vertical velocity is positive upward. The files' velocity comment says
positive is toward the lidar, as the PPI and CSM files' does; that comment
is wrong for those (raw_lidar_archive_fetcher._truck_azimuth), and the
stares follow the same sign as the conical scans taken between them (the
truck's DL2 files, 2022-05-13: the stares' mean velocity and the conical
scans' mean radial velocity / sin(70°) agree in sign, r = 0.28 over 35
scans), and the truck's 2022-05-29 stare under a 1.1 km cloud base shows
a +1 m/s updraft at 0.35-0.65 km, so velocity is used as stored.

The first gates (clamps_common.BLIND_GATES) are left out, as for every lidar product.

Some files on THREDDS are cut short (clampsdlfpC1.b1.20230609: 32 KB of the
52 MB its header expects); those are reported as incomplete rather than as
a read error.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from providers.nssl import paths
from providers.nssl.archive import clamps_common
from providers.nssl.archive.clamps_common import BLIND_GATES
from providers.nssl.archive.raw_lidar_archive_fetcher import (
    _NAMES, LidarAsset, RawLidarSource, _values, discover_raw_lidar,
)

log = logging.getLogger(__name__)

STARE_CACHE_DIR = Path.home() / ".cache" / "storm" / "raw_lidar"
STARE_MIN_ELEVATION = 85.0       # rays steeper than this count as vertical

KNOWN_STARE_SOURCES = tuple(
    RawLidarSource(f"{platform}-FP", instrument, directory,
                   paths.raw_lidar_stream(prefix, paths.RAW_LIDAR_STARE_PRODUCT, unit),
                   paths.RAW_LIDAR_STARE_PRODUCT, mobile, *_NAMES.get(instrument, ("", "")))
    for platform, instrument, directory, prefix, unit, mobile in paths.RAW_LIDAR_PLATFORMS
)


@dataclass
class LidarStare:
    """One lidar's vertical rays for the session, in time order. `fields`
    are (ray, gate) arrays, NaN where missing, with their units: "w" (m/s,
    up positive) and "backscatter" (log10) in float16, "intensity" (SNR + 1)
    in float32 for its thresholds. A day of one-second stares is 86,400 rays
    of 400 gates: about 280 MB like this, three times that as masked float32."""
    instrument: str
    name: str
    time_epoch: np.ndarray
    height_m: np.ndarray
    fields: dict
    cloud_base_m: np.ndarray
    files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def __len__(self) -> int:
        return int(self.time_epoch.size)


_TRUNCATED = re.compile(r"truncated file: eof = (\d+).*stored_eof = (\d+)")


def incomplete_file_message(exc: BaseException) -> str | None:
    """"file incomplete on THREDDS (32 KB of 52 MB)" for a file cut short
    on the server, else None."""
    match = _TRUNCATED.search(str(exc))
    if not match:
        return None
    have, want = (int(n) for n in match.groups())

    def size(n):
        return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"
    return f"file incomplete on THREDDS ({size(have)} of {size(want)})"


def _gate_field(variable, rows, order) -> np.ndarray:
    """The rows wanted (in file order, so only they are read), past the
    blind gates, then time-sorted."""
    values = np.asarray(variable.isel(time=rows, height=slice(BLIND_GATES, None)).values)[order]
    values = values.astype(np.float32, copy=False)
    values[~np.isfinite(values) | (values == -999) | (np.abs(values) > 1e30)] = np.nan
    return values


def parse_stare(path, source: RawLidarSource, span: tuple[float, float] | None = None) -> LidarStare:
    """One file's vertical rays; with *span* (epoch s), only those in it."""
    import xarray as xr
    with Path(path).open("rb") as stream:
        engine = "h5netcdf" if stream.read(8) == b"\x89HDF\r\n\x1a\n" else "scipy"
    with xr.open_dataset(path, engine=engine, decode_times=False) as ds:
        if "height" not in ds or "velocity" not in ds:
            raise ValueError("Not a fixed-point stare file: no height axis or velocity")
        epoch = float(ds["base_time"].values) + _values(ds["time_offset"])
        elevation = _values(ds["elevation"]) if "elevation" in ds else np.full(epoch.size, 90.0)
        wanted = np.isfinite(epoch) & (epoch > 0) & (elevation >= STARE_MIN_ELEVATION)
        if span is not None:
            wanted &= (epoch >= span[0]) & (epoch < span[1])
        rows = np.flatnonzero(wanted)
        order = np.argsort(epoch[rows], kind="stable")
        keep = rows[order]
        height = _values(ds["height"])[BLIND_GATES:]
        if not height.size:
            raise ValueError("Stare file has no gates past the blind zone")
        units = (ds["height"].attrs.get("units", "") or "").lower().split()
        height = height * 1000 if units and units[0] in ("km", "kilometers", "kilometres") else height
        fields = {"w": {"data": _gate_field(ds["velocity"], rows, order).astype(np.float16), "units": "m/s",
                        "label": "Vertical velocity (up +)"}}
        if "intensity" in ds:
            fields["intensity"] = {"data": _gate_field(ds["intensity"], rows, order), "units": "SNR + 1",
                                   "label": "Intensity"}
        if "backscatter" in ds:
            backscatter = _gate_field(ds["backscatter"], rows, order)
            backscatter[~(backscatter > 0)] = np.nan
            with np.errstate(invalid="ignore"):
                logged = np.log10(backscatter, out=backscatter)
            fields["backscatter"] = {"data": logged.astype(np.float16),
                                     "units": "log10 km⁻¹ sr⁻¹", "label": "Backscatter"}
        cloud = (_values(ds["cbh"])[keep] if "cbh" in ds else np.full(keep.size, np.nan))
        cloud_units = (ds["cbh"].attrs.get("units", "km") if "cbh" in ds else "km").lower().split()
        cloud = cloud * 1000 if cloud_units and cloud_units[0].startswith("k") else cloud
        cloud[~(cloud > 0)] = np.nan
    return LidarStare(source.instrument, source.name or source.instrument, epoch[keep], height,
                      fields, cloud, [Path(path).name])


def merge_stares(parts: list[LidarStare]) -> LidarStare | None:
    """One lidar's files as one time series (the truck's DL1 and DL2
    streams, the session's two UTC days). Files on another height grid
    than the first are left out, with a warning."""
    parts = [p for p in parts if len(p)]
    if not parts:
        return None
    base = max(parts, key=len)
    same = [p for p in parts if p.height_m.shape == base.height_m.shape
            and np.allclose(p.height_m, base.height_m, equal_nan=True)]
    kept = {id(p) for p in same}
    warnings = [f"{', '.join(p.files)}: another height grid, not shown" for p in parts if id(p) not in kept]
    times = np.concatenate([p.time_epoch for p in same])
    order = np.argsort(times, kind="stable")
    names = [n for n in base.fields if all(n in p.fields for p in same)]
    fields = {n: {**base.fields[n], "data": np.concatenate([p.fields[n]["data"] for p in same])[order]}
              for n in names}
    return LidarStare(base.instrument, base.name, times[order], base.height_m, fields,
                      np.concatenate([p.cloud_base_m for p in same])[order],
                      [f for p in same for f in p.files], warnings)


class ArchiveLidarStareFetcher(QObject):
    """Finds each lidar's stare files for the session, and loads one lidar's
    when the stares window asks for it."""
    found = pyqtSignal(dict)          # instrument -> [LidarAsset]; None: couldn't check
    ready = pyqtSignal(str, object)   # instrument, LidarStare
    failed = pyqtSignal(str, str)     # instrument, why there's nothing to show
    progress = pyqtSignal(str, str)   # instrument, how its download is going

    sources = KNOWN_STARE_SOURCES

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        from archive.session import activity_from, session_bounds
        self._day = session_date.date()
        # the rays that belong to this session, as for the scans: from 12Z on
        # its day (earlier ones are the previous evening's) to its end
        start, end = session_bounds(session_date)
        self._span = ((start + activity_from()).timestamp(), end.timestamp())
        self._cancel = threading.Event()
        self._assets: dict[str, list[LidarAsset]] = {}
        self._loaded: dict[str, LidarStare] = {}
        self._loading: set[str] = set()

    def cancel(self) -> None:
        self._cancel.set()

    def name(self, instrument: str) -> str:
        return next((s.name for s in self.sources if s.instrument == instrument and s.name), instrument)

    def fetch(self) -> None:
        threading.Thread(target=self._find, daemon=True, name="lidar-stares-find").start()

    def _find(self) -> None:
        # the session runs into the next UTC morning, and the files are daily
        days = (self._day, self._day + timedelta(days=1))
        found: dict[str, list | None] = {}
        for source in self.sources:
            try:
                assets = [a for n, day in enumerate(days) for a in self._discover(source, day, n)]
            except Exception as exc:  # noqa: BLE001
                log.warning("Lidar stares %s: couldn't list: %s", source.platform_id, exc)
                found.setdefault(source.instrument, None)
                continue
            if assets:
                found[source.instrument] = (found.get(source.instrument) or []) + assets
        self._assets = {k: v for k, v in found.items() if v}
        if not self._cancel.is_set():
            self.found.emit(found)

    @staticmethod
    def _discover(source, day, n):
        try:
            return discover_raw_lidar(source, day)
        except Exception:
            if n == 0:
                raise
            return []         # the next day's folder listing isn't needed

    def load(self, instrument: str) -> None:
        """The lidar's stares, once: ready, or failed with the reason."""
        if instrument in self._loaded:
            self.ready.emit(instrument, self._loaded[instrument])
            return
        if instrument in self._loading:
            return
        self._loading.add(instrument)
        threading.Thread(target=self._load, args=(instrument,), daemon=True, name="lidar-stares-load").start()

    def _progress_reporter(self, instrument: str, n: int, files: int):
        """download_to_cache's progress callback, as progress() every 5 MB:
        a day of stares can be hundreds of MB."""
        step = 5 * 2**20
        sent = [-step]

        def report(size: int, total: int | None) -> None:
            if size - sent[0] < step:
                return
            sent[0] = size
            got = f"{size / 2**20:.0f}" + (f" of {total / 2**20:.0f} MB" if total else " MB")
            which = f"file {n} of {files}: " if files > 1 else ""
            self.progress.emit(instrument, f"Downloading {self.name(instrument)}'s stares ({which}{got})…")
        return report

    def _load(self, instrument: str) -> None:
        parts, problems = [], []
        try:
            assets = self._assets.get(instrument, [])
            for n, asset in enumerate(assets, 1):
                if self._cancel.is_set():
                    return
                try:
                    path = clamps_common.download_to_cache(
                        "raw lidar", asset.url, STARE_CACHE_DIR, asset.filename, cancel=self._cancel,
                        progress=self._progress_reporter(instrument, n, len(assets)))
                    parts.append(parse_stare(path, asset.source, self._span))
                except clamps_common.Cancelled:
                    return
                except Exception as exc:  # noqa: BLE001
                    reason = incomplete_file_message(exc) or str(exc)
                    log.warning("Lidar stares %s: %s: %s", instrument, asset.filename, reason)
                    problems.append(f"{asset.filename}: {reason}")
            stare = merge_stares(parts)
            if stare is None:
                self.failed.emit(instrument, "; ".join(problems) or "no vertical rays this session")
                return
            stare.warnings[:0] = problems
            self._loaded[instrument] = stare
            self.ready.emit(instrument, stare)
        finally:
            self._loading.discard(instrument)
