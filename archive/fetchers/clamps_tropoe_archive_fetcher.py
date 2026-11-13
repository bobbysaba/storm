"""CLAMPS TROPoe retrievals (temperature and humidity against height) for the
archive sounding viewer.

TROPoe output from the trailers' AERI / MWR maps onto core/sounding.py's
Sounding / SoundingSet. STORM doesn't run TROPoe: it's an already-processed
product that some deployments have. Each trailer can have several variants
(AERI + MWR, AERI only, MWR only; v1 / v2 processing), tried in
paths.CLAMPS_TROPOE_VARIANTS order; the first with a file for the date wins.

File names carry the time the day's run started (…20220525.001005.nc) and
end in .nc or .cdf, and a variant's folder also holds other streams' files,
so files are found by listing the folder and matching the datastream and
date.

TROPoe retrieves no wind (AERI and MWR aren't Doppler), so u / v are NaN:
absent, not calm.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from archive.fetchers import clamps_common
from core.sounding import Sounding, SoundingSet
from data import thredds_paths as paths

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60


@dataclass(frozen=True)
class ClampsTropoePlatform:
    platform_id: str    # e.g. "CLAMPS2"
    platform_dir: str   # under FRDD/CLAMPS, e.g. "clamps/clamps2"
    unit_suffix: str    # datastream suffix, e.g. "C2"


# TROPoe needs the AERI / MWR, which are on the trailers, not the lidar truck.
KNOWN_CLAMPS_TROPOE_PLATFORMS: tuple[ClampsTropoePlatform, ...] = tuple(
    ClampsTropoePlatform(*row) for row in paths.CLAMPS_TROPOE_PLATFORMS)


def _files_for_date(platform: ClampsTropoePlatform, date_str: str) -> tuple[str, list[str]] | None:
    """(datastream, file URLs) of the first variant with files for the date."""
    for variant in paths.CLAMPS_TROPOE_VARIANTS:
        datastream = paths.clamps_tropoe_stream(variant, platform.unit_suffix)
        folder = paths.clamps_processed(platform.platform_dir, datastream)
        prefix = f"{datastream}.{date_str}."
        names = sorted(n for n in clamps_common.list_folder(folder)
                       if n.startswith(prefix) and n.endswith((".nc", ".cdf")))
        if names:
            return datastream, [paths.file_url(f"{folder}/{n}") for n in names]
    return None


def parse_clamps_tropoe_netcdf(data: bytes, platform_id: str) -> list[Sounding]:
    """Parse a TROPoe file into Soundings.

    Only rows with qc_flag == 0 and converged_flag == 1 are kept: a rejected
    retrieval still carries plausible-looking values (a flat, unconverged
    profile repeating the prior), not NaN, so the flags must be checked.
    """
    import warnings

    import numpy as np
    import xarray as xr

    engine = "scipy" if data.startswith(b"CDF") else "h5netcdf"
    with warnings.catch_warnings():
        # the covariance variables repeat a dimension ("arb", "arb"); unused here
        warnings.filterwarnings("ignore", message="Duplicate dimension names")
        ds = xr.open_dataset(io.BytesIO(data), engine=engine, decode_times=False)
    with ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        height_m = np.asarray(ds["height"].values, dtype="float64") * 1000.0
        temperature = np.asarray(ds["temperature"].values, dtype="float64")
        dewpoint = np.asarray(ds["dewpt"].values, dtype="float64")
        pressure = np.asarray(ds["pressure"].values, dtype="float64")
        qc_flag = np.asarray(ds["qc_flag"].values, dtype="float64")
        converged_flag = np.asarray(ds["converged_flag"].values, dtype="float64")
        lat = float(ds["lat"].values)
        lon = float(ds["lon"].values)
        alt = float(ds["alt"].values)

    height_msl_m = height_m + alt
    no_wind = np.full(height_msl_m.shape, np.nan, dtype="float64")

    soundings: list[Sounding] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        if not np.isfinite(t_off):
            continue
        if qc_flag[i] != 0.0 or converged_flag[i] != 1.0:
            continue
        valid_time = datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc)
        soundings.append(Sounding(
            lat=lat,
            lon=lon,
            valid_time=valid_time,
            slot_offset=len(soundings),
            label=valid_time.strftime("%H%MZ %d %b"),
            pressure=pressure[i],
            temperature=temperature[i],
            dewpoint=dewpoint[i],
            u_wind=no_wind,
            v_wind=no_wind,
            height=height_msl_m,
        ))
    return soundings


def fetch_clamps_tropoe_soundings(archive_time: datetime) -> SoundingSet | None:
    """The first trailer with a usable retrieval on this UTC date, or None.

    At most one trailer: the two are separate sites, and a second one's
    profiles would quietly mix two places into what looks like one series.
    Raises when no trailer has data and a listing or download failed, so
    the caller can tell "none" from "couldn't ask"."""
    date_str = archive_time.strftime("%Y%m%d")
    failure: Exception | None = None
    for platform in KNOWN_CLAMPS_TROPOE_PLATFORMS:
        try:
            found = _files_for_date(platform, date_str)
        except Exception as exc:  # noqa: BLE001 - listing failed; try the next trailer
            failure = exc
            continue
        if found is None:
            continue
        datastream, urls = found
        soundings: list[Sounding] = []
        for url in urls:
            try:
                data = clamps_common.read("clamps profiles", url, timeout=_REQUEST_TIMEOUT)
                soundings += parse_clamps_tropoe_netcdf(data, platform.platform_id)
            except Exception as exc:  # noqa: BLE001 - network or unexpected schema
                log.warning("CLAMPS TROPoe %s: %s failed: %s", platform.platform_id, url, exc)
                failure = exc
        if not soundings:
            continue
        soundings.sort(key=lambda s: s.valid_time)
        for idx, snd in enumerate(soundings):
            snd.slot_offset = idx
        variant = datastream.split(".", 1)[1].rsplit(".", 1)[0]     # e.g. "aeri_mwr.v1"
        return SoundingSet(
            lat=soundings[0].lat,
            lon=soundings[0].lon,
            elevation=float(soundings[0].height[0]),
            fetch_time=archive_time,
            soundings=soundings,
            station_id=platform.platform_id,
            station_name=f"{platform.platform_id} TROPoe ({variant})",
            source="clamps_tropoe",
        )
    if failure is not None:
        raise failure
    return None
