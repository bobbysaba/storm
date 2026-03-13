# core/radar_scan.py
# record representing a single decoded nexrad level 3 radar scan.

# import required packages
from datetime import datetime
import numpy as np


# radar scan record
class RadarScan:
    # create a new radar scan
    def __init__(
        self,
        site,
        product,
        scan_time,
        data,
        lats,
        lons,
        vmin,
        vmax,
        units,
        colormap,
        az_offset=0.0,
    ):
        # assign site id
        self.site = site
        # assign product code
        self.product = product
        # assign scan time
        self.scan_time = scan_time
        # assign data array
        self.data = data
        # assign latitude grid
        self.lats = lats
        # assign longitude grid
        self.lons = lons
        # assign display min
        self.vmin = vmin
        # assign display max
        self.vmax = vmax
        # assign units label
        self.units = units
        # assign colormap name
        self.colormap = colormap
        # assign azimuth offset
        self.az_offset = az_offset

    # how many seconds old this scan is
    @property
    def age_seconds(self):
        # local import to avoid global timezone import
        from datetime import timezone
        # compute age
        return (datetime.now(timezone.utc) - self.scan_time).total_seconds()

    # whether the scan is older than 10 minutes
    @property
    def is_stale(self):
        # compare age against 10 minutes
        return self.age_seconds > 600

    # human-readable label for ui display
    @property
    def label(self):
        # get friendly product name
        name = PRODUCT_META.get(self.product, {}).get("name", self.product)
        # return label
        return f"{self.site} {name} {self.scan_time.strftime('%H:%M')}Z"


# ── Product Metadata ──────────────────────────────────────────────────────────

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
        "vmin":     -100.0,
        "vmax":     100.0,
        "colormap": "nws_vel",
    },
    "N0C": {
        "name":     "Correlation Coefficient",
        "units":    "",
        "vmin":     0.0,
        "vmax":     1.0,
        "colormap": "nws_cc",
    },
}
