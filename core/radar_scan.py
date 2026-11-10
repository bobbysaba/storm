
import math
from datetime import datetime

import numpy as np

_R_SPHERE_M = 6371000.0


# radar scan record
class RadarScan:
    # earth model used to place gates; part of the render index cache key
    geodesy = "sphere"

    # create a new radar scan
    def __init__(
        self,
        site,
        product,
        scan_time,
        raw,
        vmin,
        vmax,
        units,
        colormap,
        radar_lat,
        radar_lon,
        azimuths_deg,
        first_gate_m,
        gate_width_m,
        value_table=None,
    ):
        # assign site id
        self.site = site
        # assign product code
        self.product = product
        # assign scan time
        self.scan_time = scan_time
        # assign stored gate array, shaped (num_azimuths, num_gates): packed
        # uint8 codes when value_table is set, otherwise float values
        self.raw = raw
        # assign code -> physical value table (None for float scans)
        self.value_table = value_table
        # assign display min
        self.vmin = vmin
        # assign display max
        self.vmax = vmax
        # assign units label
        self.units = units
        # assign colormap name
        self.colormap = colormap
        # assign radar location
        self.radar_lat = float(radar_lat)
        self.radar_lon = float(radar_lon)
        # assign center azimuth of each radial (degrees clockwise from north)
        self.azimuths_deg = np.asarray(azimuths_deg, dtype=np.float32)
        # assign range to the center of the first gate, and gate spacing
        self.first_gate_m = float(first_gate_m)
        self.gate_width_m = float(gate_width_m)

    # physical values as float32, NaN where there is no data
    @property
    def data(self):
        if self.value_table is not None:
            return self.value_table[self.raw]
        return self.raw.astype(np.float32)

    # range to the center of each gate, in meters
    @property
    def ranges_m(self):
        return self.first_gate_m + self.gate_width_m * np.arange(self.raw.shape[1], dtype=np.float64)

    # azimuth of the first radial
    @property
    def az_offset(self):
        return float(self.azimuths_deg[0])

    # lon/lat of points at the given azimuths (deg) and ranges (m) from the
    # radar, via the spherical forward-geodesic formula (matches py-art /
    # GR2Analyst).  Inputs broadcast against each other.
    def gate_lonlat(self, az_deg, range_m):
        az = np.deg2rad(np.asarray(az_deg, dtype=np.float64))
        delta = np.asarray(range_m, dtype=np.float64) / _R_SPHERE_M
        lat1 = math.radians(self.radar_lat)
        lon1 = math.radians(self.radar_lon)
        sin_l1 = math.sin(lat1)
        cos_l1 = math.cos(lat1)
        sin_d = np.sin(delta)
        cos_d = np.cos(delta)
        sin_lat2 = sin_l1 * cos_d + cos_l1 * sin_d * np.cos(az)
        lat2 = np.arcsin(np.clip(sin_lat2, -1.0, 1.0))
        lon2 = lon1 + np.arctan2(np.sin(az) * sin_d * cos_l1, cos_d - sin_l1 * sin_lat2)
        return np.rad2deg(lon2), np.rad2deg(lat2)

    # inverse of gate_lonlat: (range_m, azimuth_deg) from the radar to each
    # lon/lat, azimuth in degrees clockwise from true north
    def polar_from_lonlat(self, lon, lat):
        phi1 = math.radians(self.radar_lat)
        lam1 = math.radians(self.radar_lon)
        phi2 = np.deg2rad(lat)
        lam2 = np.deg2rad(lon)
        dphi = phi2 - phi1
        dlam = lam2 - lam1
        a = np.sin(dphi / 2.0) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2.0) ** 2
        range_m = 2.0 * _R_SPHERE_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
        y = np.sin(dlam) * np.cos(phi2)
        x = math.cos(phi1) * np.sin(phi2) - math.sin(phi1) * np.cos(phi2) * np.cos(dlam)
        az_deg = np.rad2deg(np.arctan2(y, x)) % 360.0
        return range_m, az_deg

    # per-gate latitude grid, computed on demand (not stored)
    @property
    def lats(self):
        return self.gate_lonlat(self.azimuths_deg[:, None], self.ranges_m[None, :])[1]

    # per-gate longitude grid, computed on demand (not stored)
    @property
    def lons(self):
        return self.gate_lonlat(self.azimuths_deg[:, None], self.ranges_m[None, :])[0]

    # hashable identity of this scan's content, used to cache rendered images
    @property
    def cache_key(self):
        return (self.site, self.product, self.scan_time, self.raw.shape)

    # how many seconds old this scan is
    @property
    def age_seconds(self):
        # local import to avoid global timezone import
        from datetime import timezone
        # compute age
        return (datetime.now(timezone.utc) - self.scan_time).total_seconds()

    # human-readable label for ui display
    @property
    def label(self):
        # get friendly product name
        name = PRODUCT_META.get(self.product, {}).get("name", self.product)
        # return label
        return f"{self.site} {name} {self.scan_time.strftime('%H:%M')}Z"



PRODUCT_META = {
    "N0B": {
        "name":     "Base Reflectivity (SR)",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":     90.0,
        "colormap": "nws_ref",
    },
    "N0Q": {
        "name":     "Base Reflectivity",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":     90.0,
        "colormap": "nws_ref",
    },
    "N0U": {
        "name":     "Base Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    # Super-res base velocity (alternate THREDDS product code — same format as N0U)
    "NBU": {
        "name":     "Base Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    # Legacy 16-level storm-relative velocity fallback (kt, ±64 kt range)
    "N0S": {
        "name":     "Storm Relative Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    "N0C": {
        "name":     "Correlation Coefficient",
        "units":    "",
        "vmin":     0.0,
        "vmax":     1.0,
        "colormap": "nws_cc",
    },
    "N0X": {
        "name":     "Diff. Reflectivity",
        "units":    "dB",
        "vmin":     -4.0,
        "vmax":      8.0,
        "colormap": "nws_zdr",
    },
    "N0K": {
        "name":     "Specific Diff. Phase",
        "units":    "deg/km",
        "vmin":     -2.0,
        "vmax":     10.0,
        "colormap": "nws_kdp",
    },
}
