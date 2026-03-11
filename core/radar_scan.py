# core/radar_scan.py
# Dataclass representing a single decoded NEXRAD Level 3 radar scan.

from dataclasses import dataclass, field
from datetime import datetime
import numpy as np


@dataclass
class RadarScan:
    """
    A single decoded NEXRAD Level 3 scan ready for display.

    Attributes:
        site        — 4-letter NEXRAD site ID (e.g. "KTLX")
        product     — product code (e.g. "N0Q", "N0U")
        scan_time   — UTC time of the scan
        data        — 2D numpy array of data values (reflectivity dBZ or velocity kt)
        lats        — 2D numpy array of latitudes matching data shape
        lons        — 2D numpy array of longitudes matching data shape
        vmin        — display minimum value
        vmax        — display maximum value
        units       — string label for the data units
        colormap    — name hint for the colormap to use
    """
    site:      str
    product:   str
    scan_time: datetime
    data:      np.ndarray
    lats:      np.ndarray
    lons:      np.ndarray
    vmin:      float
    vmax:      float
    units:     str
    colormap:  str
    az_offset: float = 0.0   # azimuth of row 0 in degrees (0 = North, clockwise)

    @property
    def age_seconds(self) -> float:
        """How many seconds old this scan is."""
        from datetime import timezone
        return (datetime.now(timezone.utc) - self.scan_time).total_seconds()

    @property
    def is_stale(self) -> bool:
        """True if the scan is older than 10 minutes."""
        return self.age_seconds > 600

    @property
    def label(self) -> str:
        """Human-readable label for UI display."""
        name = PRODUCT_META.get(self.product, {}).get("name", self.product)
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
