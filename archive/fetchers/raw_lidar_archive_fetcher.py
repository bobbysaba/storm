"""Raw Doppler lidar rays from the CLAMPS trailers' and the lidar truck's PPI
and CSM (continuous scan mode) files: the scans STORM draws on the map
(core/lidar_scans.py picks them out). Fixed-point stares and RHI files
aren't read. These are measured radial velocities and backscatter, not the
wind retrievals of clamps_wind_archive_fetcher.py.

Files come from NSSL THREDDS (FRDD/CLAMPS/<platform>/ingested), found by
listing the folder, and are kept in a disk cache (clamps_common).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

import config
from archive.fetchers import clamps_common
from archive.fetchers.vehicle_obs_archive_fetcher import _USER_AGENT
from data import thredds_paths as paths


@dataclass(frozen=True)
class RawLidarSource:
    platform_id: str   # e.g. "DLTRUCK1-DL2-PPI"
    instrument: str    # the physical lidar, e.g. "DLTRUCK1": one lidar has a
                       # source per scan-mode product, and the truck's was also
                       # published under two stream names (DL1, DL2)
    platform_dir: str
    datastream: str
    product: str       # "ppi" / "csm"
    mobile: bool       # the truck: no position in its files, azimuths relative to it

    @property
    def path(self) -> str:
        return paths.clamps_ingested(self.platform_dir, self.datastream)


# DL1 and DL2 are the same lidar on the truck. On days both streams have a
# file for the same scan mode the files differ, so both are kept.
KNOWN_RAW_LIDAR_SOURCES = tuple(
    RawLidarSource(f"{platform}-{product.upper()}", instrument, directory,
                   paths.raw_lidar_stream(prefix, product, unit), product, mobile)
    for platform, instrument, directory, prefix, unit, mobile in paths.RAW_LIDAR_PLATFORMS
    for product in paths.RAW_LIDAR_PRODUCTS
)


@dataclass(frozen=True)
class LidarAsset:
    source: RawLidarSource
    filename: str

    @property
    def url(self) -> str:
        return paths.file_url(f"{self.source.path}/{self.filename}")


def discover_raw_lidar(source: RawLidarSource, day: date) -> list[LidarAsset]:
    """The source's files for one UTC day. Names start with the datastream
    and date ("clampsdlppiC1.b1.20230609.133415.cdf"); the time differs from
    file to file, so the folder is listed rather than a name guessed."""
    prefix = f"{source.datastream}.{day:%Y%m%d}."
    return [LidarAsset(source, name) for name in sorted(clamps_common.list_folder(source.path))
            if name.startswith(prefix) and name.endswith(".cdf")]


class _RangeFile:
    """Read-only file over HTTP range requests, 64 KB at a time, so h5py can
    read a netCDF4 file's header without downloading the file."""

    BLOCK = 64 * 1024

    def __init__(self, url: str, timeout: float = 20):
        self.url, self.timeout, self.pos, self.size, self._blocks = url, timeout, 0, None, {}

    def _block(self, i: int) -> bytes:
        if i not in self._blocks:
            request = Request(self.url, headers={"User-Agent": _USER_AGENT,
                                                 "Range": f"bytes={i * self.BLOCK}-{(i + 1) * self.BLOCK - 1}"})
            with urlopen(request, timeout=self.timeout, context=config.NSSL_SSL_CONTEXT) as response:
                content_range = response.headers.get("Content-Range", "")
                data = response.read()
            if response.status != 206 or "/" not in content_range:
                raise OSError("server does not support range requests")
            self.size = int(content_range.rsplit("/", 1)[1])
            self._blocks[i] = data
        return self._blocks[i]

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        if self.size is None:
            self._block(0)
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, n=-1):
        if self.size is None:
            self._block(0)
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while len(out) < n and self.pos < self.size:
            i, offset = divmod(self.pos, self.BLOCK)
            chunk = self._block(i)[offset:offset + n - len(out)]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
        return bytes(out)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def read_site(asset: LidarAsset) -> dict | None:
    """A trailer's recorded site -- {lat, lon, altitude_m, description} --
    from the file's header alone (a range request or two; the file itself
    can be hundreds of MB). None when the header has no usable site or
    can't be read this way."""
    try:
        import h5py
        with h5py.File(_RangeFile(asset.url), "r") as h5:
            attrs = dict(h5.attrs)
    except Exception:  # noqa: BLE001 - the site is only a head start
        return None

    def number(name):
        value = attrs.get(name)
        try:
            value = float(np.ravel(value)[0]) if value is not None else float("nan")
        except (TypeError, ValueError, IndexError):
            return float("nan")
        return value if value > -900 else float("nan")

    lat, lon = number("Site_latitude"), number("Site_longitude")
    if not (np.isfinite(lat) and np.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0)):
        return None
    description = attrs.get("Site_description", "")
    if isinstance(description, bytes):
        description = description.decode("utf-8", "replace")
    description = str(description).strip()
    return {"lat": lat, "lon": lon, "altitude_m": number("Site_altitude"),
            "description": "" if description in ("None", "-999") else description}


@dataclass
class RawLidarRays:
    """One file's rays in time order. `fields` are (ray, gate) masked float32
    arrays with the file's attributes; positions are per ray."""
    source: RawLidarSource
    time_epoch: np.ndarray
    distance_m: np.ndarray    # slant range of each gate
    azimuth_deg: np.ndarray   # true north (the truck's rotated by its heading)
    elevation_deg: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    altitude_m: np.ndarray
    heading_deg: np.ndarray
    scan_number: np.ndarray
    fields: dict
    coordinate_source: np.ndarray
    provenance: dict
    warnings: list[str]
    azimuth_known: np.ndarray | None = None   # per ray; None = all rays alike (provenance flag)
    instrument_azimuth_deg: np.ndarray | None = None   # as stored (the truck's: relative to the truck),
                                                       # for telling scan types apart (core/lidar_scans.py)
    scans: list | None = None                 # core/lidar_scans.classify_scans, set by the loader

    @property
    def ground_geometry_valid(self) -> np.ndarray:
        """Rays that can be placed on a map: a position and a true-north
        azimuth (a truck file without a heading has neither)."""
        north = self.provenance.get("north_referenced", True)
        known = self.azimuth_known if self.azimuth_known is not None else (not self.source.mobile or north)
        return (np.isfinite(self.latitude) & np.isfinite(self.longitude)
                & np.isfinite(self.azimuth_deg) & np.isfinite(self.elevation_deg) & known)

    def rays_at(self, when: datetime, window_seconds: float = 60) -> np.ndarray:
        """Rays measured in the window_seconds up to `when`."""
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        epoch = when.timestamp()
        return np.flatnonzero((self.time_epoch <= epoch) & (self.time_epoch > epoch - window_seconds))


def _values(variable) -> np.ndarray:
    values = np.asarray(variable.values, dtype=float).copy()
    # b1 files also use undeclared -999 and netCDF default-fill sentinels
    values[~np.isfinite(values) | (values == -999) | (np.abs(values) > 1e30)] = np.nan
    return values


def _field_values(variable, valid) -> np.ma.MaskedArray:
    """A (time, range) field for the valid rays in float32 (as the files
    store it), masked where missing. In place: a day of the truck's CSM rays
    is large, and float64 copies of every field took ~0.5 GB."""
    values = np.asarray(variable.values)[valid].astype(np.float32, copy=False)
    values[~np.isfinite(values) | (values == -999) | (np.abs(values) > 1e30)] = np.nan
    return np.ma.masked_invalid(values, copy=False)


def parse_raw_lidar(path, source: RawLidarSource) -> RawLidarRays:
    import xarray as xr
    with Path(path).open("rb") as stream:
        engine = "h5netcdf" if stream.read(8) == b"\x89HDF\r\n\x1a\n" else "scipy"
    with xr.open_dataset(path, engine=engine, decode_times=False) as ds:
        epoch = float(ds["base_time"].values) + _values(ds["time_offset"])
        valid = np.flatnonzero(np.isfinite(epoch) & (epoch > 0) & (epoch < 32503680000))
        valid = valid[np.argsort(epoch[valid], kind="stable")]
        if not valid.size:
            raise ValueError("Raw lidar has no usable acquisition times")
        times = epoch[valid]
        if "range" not in ds:
            raise ValueError("Not a PPI/CSM scan file: no slant range")
        axis = _values(ds["range"])
        units = ds["range"].attrs.get("units", "").lower().strip()
        # 2017-2020 CLAMPS files label it "km AGL", though it is slant range
        # ("height = range x sin(elevation)"): the unit is the first word
        units = units.split()[0] if units else units
        if units in ("km", "kilometers", "kilometres"):
            axis *= 1000
        elif units not in ("m", "meters", "metres"):
            raise ValueError(f"Unknown raw lidar range units: {units!r}")
        if axis.ndim != 1 or not np.isfinite(axis).all() or (axis < 0).any():
            raise ValueError("Invalid lidar range axis")

        def rays(name):
            if name not in ds:
                return np.full(times.size, np.nan)
            array = _values(ds[name])
            return np.full(times.size, float(array)) if array.ndim == 0 else array[valid]

        lat, lon = rays("lat"), rays("lon")
        good = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180) & ((lat != 0) | (lon != 0))
        origin = np.where(good, "file rays", "unknown").astype("U32")
        lat[~good], lon[~good] = np.nan, np.nan
        if not source.mobile:
            site_lat = float(ds.attrs.get("Site_latitude", np.nan))
            site_lon = float(ds.attrs.get("Site_longitude", np.nan))
            if abs(site_lat) <= 90 and abs(site_lon) <= 180 and (site_lat, site_lon) != (0, 0):
                lat[~good], lon[~good], origin[~good] = site_lat, site_lon, "file site attributes"
        fields = {name: {**variable.attrs, "data": _field_values(variable, valid)}
                  for name, variable in ds.data_vars.items() if variable.dims == ("time", "range")}
        if "velocity" not in fields:
            raise ValueError("Raw lidar file has no radial velocity field")
        warnings = []
        if source.product == "csm":
            warnings.append("Continuous scan mode: the provider cautions these rays need careful interpretation.")
        azimuth = rays("azimuth")
        stored_azimuth = azimuth.copy()
        reference = {"north_referenced": True, "azimuth_reference": "as stored in the file"}
        if source.mobile:
            azimuth, reference = _truck_azimuth(azimuth, ds.attrs.get("Trailer_heading"))
            warnings.append(reference["azimuth_reference"])
            warnings.append("No platform-motion correction is applied to radial velocity.")
        return RawLidarRays(
            source, times, axis, azimuth, rays("elevation"), lat, lon, rays("alt"), rays("heading"),
            rays("snum"), fields, origin,
            {"distance_units_in_file": units, **reference}, warnings,
            instrument_azimuth_deg=stored_azimuth)


def _truck_azimuth(azimuth, trailer_heading):
    """True-north azimuths for the lidar truck.

    The truck's files store azimuth relative to the truck (0 = straight
    ahead), although the azimuth comment says "0 degrees is north". Its
    heading is in the Trailer_heading attribute but not applied, so true
    azimuth = stored + heading. Checked in the fork (2026-09-26): VAD wind
    directions from the truck's PPI scans against HRRR 80 m over 23 days /
    62 scans had a median error of 75.7 deg as stored and 10 deg rotated
    (the trailers need no rotation). Radial velocity is positive away from
    the lidar despite the files' comment, as STORM already shows it.
    A missing heading (-999, NaN, or the 0.0 default of the 2020-21 files)
    leaves the orientation unknown until the truck's compass fills it."""
    try:
        heading = float(trailer_heading)
    except (TypeError, ValueError):
        heading = float("nan")
    if not np.isfinite(heading) or heading == 0.0 or not 0 <= heading <= 360:
        return azimuth, {"north_referenced": False,
                         "azimuth_reference": "Truck heading not recorded in this file, so scan directions "
                                              "relative to north are unknown; not placed on the map."}
    return (azimuth + heading) % 360.0, {
        "north_referenced": True, "truck_heading_deg": heading,
        "azimuth_reference": f"Truck-relative azimuths rotated to true north by the recorded truck "
                             f"heading ({heading:.1f}°)."}


def load_raw_lidar(asset: LidarAsset, cache_dir, *, cancel=None, truck_track=None) -> RawLidarRays:
    """Download (or reuse) and parse one file. The truck's rays are placed
    from its GPS track (an archive.positions.PositionTrack: the session's
    one-second FOFS fixes), and oriented by its compass when the file has no
    heading."""
    path = clamps_common.download_to_cache("raw lidar", asset.url, cache_dir, asset.filename, cancel=cancel)
    clamps_common._check(cancel)
    result = parse_raw_lidar(path, asset.source)
    result.provenance.update(url=asset.url)
    if asset.source.mobile:
        _place_truck_rays(result, truck_track)
    return result


def _place_truck_rays(result: RawLidarRays, track) -> None:
    missing = ~np.isfinite(result.latitude)
    if track is not None and len(track) and missing.any():
        lat, lon, _ = track.positions_at(result.time_epoch[missing])
        result.latitude[missing], result.longitude[missing] = lat, lon
        placed = np.zeros_like(missing)
        placed[missing] = np.isfinite(lat)
        result.coordinate_source[placed] = "FOFS truck GPS within 60 s"
    if result.provenance.get("north_referenced"):
        result.heading_deg = np.full(result.time_epoch.size, result.provenance["truck_heading_deg"])
    else:
        _estimate_truck_heading(result, track)


def _estimate_truck_heading(result: RawLidarRays, track) -> None:
    """Fill a heading missing from a truck file from the truck's own compass
    (FOFS compass_dir), ray by ray: the truck can move during a file. Checked
    in the fork (2026-09-26): that compass matches the files' recorded
    Trailer_heading to a median 0.2 deg (45 scans), and heading-less scans
    rotated by it give lidar wind directions within a median 9.6 deg of HRRR
    (34 scans). Rays with no compass reading within 60 s stay unmapped."""
    heading = (track.headings_at(result.time_epoch) if track is not None
               else np.full(result.time_epoch.size, np.nan))
    known = np.isfinite(heading)
    result.heading_deg = heading
    result.azimuth_known = known
    result.azimuth_deg = np.where(known, (result.azimuth_deg + np.nan_to_num(heading)) % 360.0, result.azimuth_deg)
    if not known.any():
        result.provenance["azimuth_reference"] += " No truck compass reading covers these times either."
        result.warnings[:] = [result.provenance["azimuth_reference"] if w.startswith("Truck heading not recorded")
                              else w for w in result.warnings]
        return
    angles = np.deg2rad(heading[known])
    mean_sin, mean_cos = np.sin(angles).mean(), np.cos(angles).mean()
    typical = float(np.rad2deg(np.arctan2(mean_sin, mean_cos)) % 360.0)
    spread = float(np.rad2deg(np.sqrt(-2 * np.log(max(np.hypot(mean_sin, mean_cos), 1e-9)))))
    share = known.mean()
    message = (f"Truck heading is missing from this lidar file; estimated from the truck compass "
               f"(FOFS mesonet): {typical:.0f}°" + (f", varying ±{spread:.0f}° as the truck moved" if spread > 10 else "")
               + ("" if share == 1 else f"; {100 * (1 - share):.0f}% of rays had no compass reading and are not mapped")
               + ".")
    result.provenance.update(north_referenced=True, heading_missing_in_file=True,
                             truck_heading_estimate_deg=round(typical, 1), azimuth_reference=message)
    result.warnings[:] = [message if w.startswith("Truck heading not recorded") else w for w in result.warnings]
