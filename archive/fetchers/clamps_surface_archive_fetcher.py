"""CLAMPS trailer surface meteorology for archive playback.

FRDD/CLAMPS surface fields map onto core/observation.py's Observation, so
the trailers show on the map like the mobile mesonet. Two sources, neither
covering every trailer and date:

  - the met tower ("clampsmetC2.a1"): CLAMPS2 only, 2016-2021. Its files
    warn that wind direction "needs offset applied to account for the
    trailer heading -- this has not been applied". With nothing to check a
    correction against, its wind direction is left out; temperature,
    humidity, pressure and speed don't depend on heading;
  - the MWR's surface fields ("clampsmwr{C1,C2}.a1"): both trailers, to
    2023 (CLAMPS1 2023-06-09, CLAMPS2 2023-11-28). Its wind direction
    already includes the trailer's orientation, so it's used, wrapped to
    0-360: some files add the heading without wrapping (CLAMPS1 2021-07-29
    reads 360-718°).

The met tower comes first where it has the date (a dedicated instrument
over a sensor on the MWR); the MWR is the fallback. Both use -999 as fill
with no fill attribute. The lidar truck isn't here: its "dltruckdlsfcDL1.b1"
stream holds short ingest tests, and its one-second FOFS data already
covers it.

Files are found by listing the folder, never by guessing names: a file's
time suffix is when that day's recording started, and 658 of CLAMPS1's
1,256 MWR days (fork, 2026-09-25) have no .000000 file.

Relative humidity becomes dewpoint by Magnus-Tetens (a = 17.625,
b = 243.04; Alduchov & Eskridge 1996): both sources report RH.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from archive.fetchers import clamps_common
from core.observation import Observation
from data import thredds_paths as paths

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60
_FILL_SENTINEL_MAX = -900.0   # values at or below this are the -999 fill
_KELVIN_MEDIAN_ABOVE = 150.0  # impossible in °C (the surface record is about 57 °C)


@dataclass(frozen=True)
class ClampsSurfaceSource:
    platform_id: str            # e.g. "CLAMPS2"
    platform_dir: str           # under FRDD/CLAMPS, e.g. "clamps/clamps2"
    datastream: str             # e.g. "clampsmwrC2.a1"
    kind: str                   # "met_tower" | "mwr"
    trust_wind_direction: bool


# In preference order within each trailer.
KNOWN_CLAMPS_SURFACE_SOURCES: tuple[ClampsSurfaceSource, ...] = tuple(
    ClampsSurfaceSource(*row) for row in paths.CLAMPS_SURFACE_STREAMS)


def _files_for_date(source: ClampsSurfaceSource, date_str: str) -> list[str]:
    """File URLs this source has for the date, in time order (a restarted
    instrument writes a second file)."""
    folder = paths.clamps_ingested(source.platform_dir, source.datastream)
    prefix = f"{source.datastream}.{date_str}."
    return [paths.file_url(f"{folder}/{name}")
            for name in sorted(clamps_common.list_folder(folder))
            if name.startswith(prefix) and name.endswith((".nc", ".cdf"))]


def _dewpoint_c_from_rh(temp_c: float, rh_pct: float) -> float | None:
    if rh_pct <= 0.0 or rh_pct > 100.0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(rh_pct / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def _valid(value: float) -> bool:
    return math.isfinite(value) and value > _FILL_SENTINEL_MAX


def _wind_dir(value: float) -> float | None:
    """Direction in 0-360, or None for fill or anything past two turns."""
    return value % 360.0 if math.isfinite(value) and 0.0 <= value < 720.0 else None


def parse_clamps_surface_netcdf(data: bytes, platform_id: str,
                                trust_wind_direction: bool = True) -> list[Observation]:
    """Parse a met tower or MWR file (same core variable names) into
    Observations. lat / lon is one value per file on the MWR (a parked
    trailer) and per record on the met tower (seen varying within a file)."""
    import numpy as np
    import xarray as xr

    # met tower files are classic netCDF, MWR files HDF5-backed
    engine = "scipy" if data.startswith(b"CDF") else "h5netcdf"
    with xr.open_dataset(io.BytesIO(data), engine=engine, decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        sfc_temp = np.asarray(ds["sfc_temp"].values, dtype="float64")
        sfc_rh = np.asarray(ds["sfc_rh"].values, dtype="float64")
        sfc_pres = np.asarray(ds["sfc_pres"].values, dtype="float64")
        sfc_wspd = np.asarray(ds["sfc_wspd"].values, dtype="float64")
        sfc_wdir = np.asarray(ds["sfc_wdir"].values, dtype="float64")
        lat_raw = np.asarray(ds["lat"].values, dtype="float64")
        lon_raw = np.asarray(ds["lon"].values, dtype="float64")

    # Some files (CLAMPS1 MWR, 2023) hold sfc_temp in kelvin under a degC
    # label. A file-wide median that high can't be °C, so the file is kelvin.
    good_temp = sfc_temp[np.isfinite(sfc_temp) & (sfc_temp > _FILL_SENTINEL_MAX)]
    if good_temp.size and float(np.median(good_temp)) > _KELVIN_MEDIAN_ABOVE:
        log.info("CLAMPS surface %s: sfc_temp is kelvin despite its degC label; converted", platform_id)
        sfc_temp = np.where(sfc_temp > _FILL_SENTINEL_MAX, sfc_temp - 273.15, sfc_temp)

    per_record = lat_raw.ndim > 0
    observations: list[Observation] = []
    for i in range(time_offset.size):
        t_off = float(time_offset[i])
        temp = float(sfc_temp[i])
        if not (math.isfinite(t_off) and _valid(temp)):
            continue
        lat = float(lat_raw[i] if per_record else lat_raw)
        lon = float(lon_raw[i] if per_record else lon_raw)
        if not (_valid(lat) and _valid(lon) and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0)):
            continue
        rh, wspd, wdir, pres = (float(a[i]) for a in (sfc_rh, sfc_wspd, sfc_wdir, sfc_pres))
        observations.append(Observation(
            vehicle_id=platform_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.fromtimestamp(base_time + t_off, tz=timezone.utc),
            icon_type="lidar",
            temperature_c=temp,
            dewpoint_c=_dewpoint_c_from_rh(temp, rh) if _valid(rh) else None,
            wind_speed_ms=wspd if _valid(wspd) else None,
            wind_dir_deg=_wind_dir(wdir) if trust_wind_direction else None,
            pressure_mb=pres if _valid(pres) else None,
        ))
    observations.sort(key=lambda obs: obs.timestamp)
    return observations


def fetch_clamps_surface_observations(day: datetime) -> list[Observation]:
    """Every trailer's observations on this UTC date, from its first source
    with data (met tower before MWR), time-ordered. Raises when nothing was
    found and a listing failed."""
    date_str = day.strftime("%Y%m%d")
    by_platform: dict[str, list[Observation]] = {}
    failure: Exception | None = None
    for source in KNOWN_CLAMPS_SURFACE_SOURCES:
        if source.platform_id in by_platform:
            continue
        try:
            urls = _files_for_date(source, date_str)
        except Exception as exc:  # noqa: BLE001 - try the next source
            failure = exc
            continue
        observations: list[Observation] = []
        for url in urls:
            try:
                data = clamps_common.read("clamps surface", url, timeout=_REQUEST_TIMEOUT)
                observations += parse_clamps_surface_netcdf(data, source.platform_id,
                                                            source.trust_wind_direction)
            except Exception as exc:  # noqa: BLE001 - network or unexpected schema
                log.warning("CLAMPS surface %s (%s): %s failed: %s",
                            source.platform_id, source.kind, url, exc)
                failure = exc
        if observations:
            unique = {obs.timestamp: obs for obs in observations}   # a restart can overlap
            by_platform[source.platform_id] = [unique[t] for t in sorted(unique)]
    if not by_platform and failure is not None:
        raise failure
    return sorted((obs for rows in by_platform.values() for obs in rows), key=lambda obs: obs.timestamp)
