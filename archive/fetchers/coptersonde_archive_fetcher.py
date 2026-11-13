"""PERiLS CopterSonde (OU) ascents for the archive sounding viewer.

Each ascent file maps onto one core/sounding.py Sounding. No by-instrument
THREDDS folder exists (FRDD/UAS, FRDD/CopterSonde, FRDD/CLAMPS/CopterSonde,
FRDD/CLAMPS/campaigns/TORUS/CopterSonde and RRDD/CopterSonde are 404); the
only home is FRDD/CLAMPS/campaigns/PERiLS/<year>/CopterSonde/v1/IOP<N>/,
by Intensive Observation Period. So for a date, the year's known IOP
folders are listed and files matched by the date in their name.

One IOP folder can hold ascents from several launch sites on one day
("LakeVillage…", "PineyCreek…", "Schlater…" on 2022-03-22). They're
different places, so each site + tail number (the name before ".c1.") is
its own SoundingSet.

Units and labels found on a real file (fork):
  - tdry is kelvin and pres is pascal; converted to °C and hPa;
  - wind_u / wind_v are labeled "westward" / "northward", a non-standard
    sign convention, so u / v come from dir and wspd instead (the usual
    meteorological formula, as in core/vad.py);
  - alt's unit is bare "meter"; taken as MSL (Sounding.height's
    convention), as it reads as a GPS altitude. An assumption, not checked;
  - a flight's trailing samples carry the netCDF default fill (~9.97e36)
    in alt / pres / lat / lon, with no fill attribute; filtered by a
    magnitude bound, not just isfinite().
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

from archive.fetchers import clamps_common
from core.sounding import Sounding, SoundingSet
from data import thredds_paths as paths

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30
_FILL_MAGNITUDE_MAX = 1.0e6   # far below the ~9.97e36 fill, far above any real value
_FILENAME_RE = re.compile(r"^(?P<site>.+)\.c1\.(?P<date>\d{8})\.(?P<time>\d{6})\.cdf$")


def coptersonde_years() -> set[int]:
    """Years with known PERiLS IOP folders: other dates need no request."""
    return {int(year) for year, _ in paths.PERILS_IOPS}


def parse_coptersonde_netcdf(data: bytes, site_id: str) -> Sounding | None:
    """One ascent file (one flight) into one Sounding."""
    import numpy as np
    import xarray as xr

    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        tdry_k = np.asarray(ds["tdry"].values, dtype="float64")
        td_c = np.asarray(ds["Td"].values, dtype="float64")
        pres_pa = np.asarray(ds["pres"].values, dtype="float64")
        wspd = np.asarray(ds["wspd"].values, dtype="float64")
        wdir = np.asarray(ds["dir"].values, dtype="float64")
        alt_m = np.asarray(ds["alt"].values, dtype="float64")
        lat = np.asarray(ds["lat"].values, dtype="float64")
        lon = np.asarray(ds["lon"].values, dtype="float64")

    def ok(values):
        with np.errstate(invalid="ignore"):
            return np.isfinite(values) & (np.abs(values) < _FILL_MAGNITUDE_MAX)

    valid = np.isfinite(time_offset) & ok(tdry_k) & ok(pres_pa) & ok(alt_m) & ok(lat) & ok(lon)
    if not np.any(valid):
        return None

    spd, direction = wspd[valid], wdir[valid]
    valid_time = datetime.fromtimestamp(base_time + float(time_offset[valid][0]), tz=timezone.utc)
    return Sounding(
        lat=float(lat[valid][0]),
        lon=float(lon[valid][0]),
        valid_time=valid_time,
        slot_offset=0,
        label=f"{site_id} {valid_time:%H%MZ %d %b}",
        pressure=pres_pa[valid] / 100.0,
        temperature=tdry_k[valid] - 273.15,
        dewpoint=td_c[valid],
        u_wind=-spd * np.sin(np.deg2rad(direction)),
        v_wind=-spd * np.cos(np.deg2rad(direction)),
        height=alt_m[valid],          # assumed MSL, see the module docstring
    )


def fetch_coptersonde_soundings(archive_time: datetime) -> dict[str, SoundingSet]:
    """Every site's ascents on this UTC date, by site + tail number, up to
    and including archive_time. Most dates have none: PERiLS flew a handful
    of IOP days a year. Raises when nothing was found and a listing failed."""
    date_str = archive_time.strftime("%Y%m%d")
    by_site: dict[str, list[Sounding]] = {}
    failure: Exception | None = None
    for year, iop in paths.PERILS_IOPS:
        if int(year) != archive_time.year:
            continue
        folder = paths.coptersonde_iop(year, iop)
        try:
            names = clamps_common.list_folder(folder)
        except Exception as exc:  # noqa: BLE001 - try the other IOPs
            failure = exc
            continue
        for name in sorted(names):
            m = _FILENAME_RE.match(name)
            if not m or m.group("date") != date_str:
                continue
            start = datetime.strptime(date_str + m.group("time"), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            if start > archive_time:
                continue        # not flown yet at the clock: no download
            site_id = m.group("site")
            try:
                data = clamps_common.read("coptersonde", paths.file_url(f"{folder}/{name}"),
                                          timeout=_REQUEST_TIMEOUT)
                snd = parse_coptersonde_netcdf(data, site_id)
            except Exception as exc:  # noqa: BLE001 - skip this flight
                log.warning("CopterSonde %s failed: %s", name, exc)
                failure = exc
                continue
            if snd is not None and snd.valid_time <= archive_time:
                by_site.setdefault(site_id, []).append(snd)
    if not by_site and failure is not None:
        raise failure

    results: dict[str, SoundingSet] = {}
    for site_id, soundings in by_site.items():
        soundings.sort(key=lambda s: s.valid_time)
        for idx, snd in enumerate(soundings):
            snd.slot_offset = idx
        latest = soundings[-1]
        results[site_id] = SoundingSet(
            lat=latest.lat,
            lon=latest.lon,
            elevation=float(latest.height[0]) if latest.height.size else 0.0,
            fetch_time=archive_time,
            soundings=soundings,
            station_id=site_id,
            station_name=f"PERiLS CopterSonde ({site_id})",
            # its own source: an in-situ UAS profile is neither a CLAMPS
            # sonde launch nor a retrieval, and source keys the dialog's
            # comparison overlays
            source="coptersonde",
        )
    return results
