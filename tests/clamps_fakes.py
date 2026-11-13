"""Synthetic FRDD/CLAMPS THREDDS for the CLAMPS / CopterSonde tests: folder
listings and files served from a dict instead of the network."""
from urllib.error import HTTPError, URLError

import numpy as np
import xarray as xr

from data import thredds_paths as paths


def catalog_html(folder: str, names) -> bytes:
    """A THREDDS catalog page listing these files (and a sub-folder link,
    which isn't a file)."""
    rows = "".join(f'<a href="catalog.html?dataset={folder}/{n}"><tt>{n}</tt></a>\n' for n in names)
    rows += f'<a href="sub/catalog.html"><tt>sub/</tt></a>\n'
    return f"<html><body>{rows}</body></html>".encode()


def not_found(url="https://example.invalid"):
    return HTTPError(url, 404, "Not Found", {}, None)


class FakeThredds:
    """Install with monkeypatch: folders map to file names, file URLs to bytes
    (or an exception to raise). Unknown URLs are a 404. `requests` records
    every URL asked for."""

    def __init__(self, monkeypatch):
        from archive.fetchers import clamps_common
        self.folders: dict[str, list[str]] = {}
        self.files: dict[str, object] = {}
        self.broken_folders: set[str] = set()
        self.requests: list[str] = []
        monkeypatch.setattr(clamps_common, "read", self._read)

    def add(self, folder: str, name: str, data) -> str:
        self.folders.setdefault(folder, []).append(name)
        url = paths.file_url(f"{folder}/{name}")
        self.files[url] = data
        return url

    def _read(self, kind, url, timeout=60):
        self.requests.append(url)
        for folder, names in self.folders.items():
            if url == paths.catalog_url(folder):
                if folder in self.broken_folders:
                    raise URLError("timed out")
                return catalog_html(folder, names)
        for folder in self.broken_folders:
            if url == paths.catalog_url(folder):
                raise URLError("timed out")
        data = self.files.get(url)
        if data is None:
            raise not_found(url)
        if isinstance(data, Exception):
            raise data
        return data

    def file_requests(self):
        return [u for u in self.requests if "/fileServer/" in u]


def netcdf_bytes(tmp_path, data_vars: dict, name="f.nc") -> bytes:
    path = tmp_path / name
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")
    return path.read_bytes()


def wind_file(tmp_path, base_time, time_offset, height_km, wspd, wdir, rms=None, r_sq=None,
              intensity=None, wspd_sigma=None) -> bytes:
    """A dlvad / dlcsmwinds file: no fill attributes, as on THREDDS."""
    v = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "height": ("height", np.array(height_km, dtype="float64")),
        "wspd": (("time", "height"), np.array(wspd, dtype="float32")),
        "wdir": (("time", "height"), np.array(wdir, dtype="float32")),
    }
    for key, value in (("rms", rms), ("r_sq", r_sq), ("intensity", intensity), ("wspd_sigma", wspd_sigma)):
        if value is not None:
            v[key] = (("time", "height"), np.array(value, dtype="float32"))
    return netcdf_bytes(tmp_path, v, "wind.cdf")


def tropoe_file(tmp_path, base_time, time_offset, height_km, temperature, dewpt, pressure,
                qc_flag, converged_flag, lat=29.4, lon=-95.0, alt=20.0, name="tropoe.nc") -> bytes:
    v = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "height": ("height", np.array(height_km, dtype="float64")),
        "temperature": (("time", "height"), np.array(temperature, dtype="float32")),
        "dewpt": (("time", "height"), np.array(dewpt, dtype="float32")),
        "pressure": (("time", "height"), np.array(pressure, dtype="float32")),
        "qc_flag": ("time", np.array(qc_flag, dtype="float32")),
        "converged_flag": ("time", np.array(converged_flag, dtype="float32")),
        "lat": ((), np.float32(lat)),
        "lon": ((), np.float32(lon)),
        "alt": ((), np.float32(alt)),
    }
    return netcdf_bytes(tmp_path, v, name)


def surface_file(tmp_path, base_time, time_offset, sfc_temp, sfc_rh, sfc_pres, sfc_wspd, sfc_wdir,
                 lat=29.4, lon=-95.0, classic=False, name="sfc.cdf") -> bytes:
    """An MWR (one lat / lon per file) or met tower (lat / lon per record,
    classic netCDF when `classic`) surface file."""
    per_record = np.ndim(lat) > 0
    v = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "sfc_temp": ("time", np.array(sfc_temp, dtype="float32")),
        "sfc_rh": ("time", np.array(sfc_rh, dtype="float32")),
        "sfc_pres": ("time", np.array(sfc_pres, dtype="float32")),
        "sfc_wspd": ("time", np.array(sfc_wspd, dtype="float32")),
        "sfc_wdir": ("time", np.array(sfc_wdir, dtype="float32")),
        "lat": ("time", np.array(lat, dtype="float32")) if per_record else ((), np.float32(lat)),
        "lon": ("time", np.array(lon, dtype="float32")) if per_record else ((), np.float32(lon)),
    }
    path = tmp_path / name
    if classic:
        v["base_time"] = ((), np.int32(base_time))
        xr.Dataset(v).to_netcdf(path, engine="scipy")
    else:
        xr.Dataset(v).to_netcdf(path, engine="h5netcdf")
    return path.read_bytes()


def coptersonde_file(tmp_path, base_time, time_offset, tdry_k, td_c, pres_pa, wspd, wdir, alt_m,
                     lat, lon, name="copter.cdf") -> bytes:
    v = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "tdry": ("time", np.array(tdry_k, dtype="float64")),
        "Td": ("time", np.array(td_c, dtype="float64")),
        "pres": ("time", np.array(pres_pa, dtype="float64")),
        "wspd": ("time", np.array(wspd, dtype="float64")),
        "dir": ("time", np.array(wdir, dtype="float64")),
        "alt": ("time", np.array(alt_m, dtype="float64")),
        "lat": ("time", np.array(lat, dtype="float64")),
        "lon": ("time", np.array(lon, dtype="float64")),
    }
    return netcdf_bytes(tmp_path, v, name)


SKEWT = """%TITLE%
NSSL_LIDAR 220524/0036
%RAW%
886.0,1074.68,22.0,15.15,120.0,19.44
871.0,1222.53,20.4,14.97,114.47,33.50
856.0,1372.9,18.7,14.64,114.85,35.50
841.0,1524.45,17.5,14.39,126.95,38.61
826.0,1678.38,16.2,14.13,130.54,39.29
%END%
"""
