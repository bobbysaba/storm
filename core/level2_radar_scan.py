
import numpy as np

from core.radar_scan import RadarScan



L2_PRODUCTS: dict[str, dict] = {
    "reflectivity": {
        "label":    "Reflectivity (REF)",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":      90.0,
        "colormap": "nws_ref",
    },
    "velocity": {
        "label":    "Velocity (VEL)",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    "cross_correlation_ratio": {
        "label":    "Corr. Coefficient (CC)",
        "units":    "",
        "vmin":      0.0,
        "vmax":      1.0,
        "colormap": "nws_cc",
    },
    "differential_reflectivity": {
        "label":    "Diff. Reflectivity (ZDR)",
        "units":    "dB",
        "vmin":     -4.0,
        "vmax":      8.0,
        "colormap": "nws_zdr",
    },
    "spectrum_width": {
        "label":    "Spectrum Width (SW)",
        "units":    "kt",
        "vmin":      0.0,
        "vmax":     30.0,
        "colormap": "nws_sw",
    },
    "differential_phase": {
        "label":    "Differential Phase (PHI)",
        "units":    "deg",
        "vmin":      0.0,
        "vmax":    360.0,
        "colormap": "nws_phi",
    },
    "specific_differential_phase": {
        "label":    "Specific Diff. Phase (KDP)",
        "units":    "deg/km",
        "vmin":     -2.0,
        "vmax":     10.0,
        "colormap": "nws_kdp",
    },
    "clutter_filter_power_removed": {
        "label":    "Clutter Filter Power Removed (CFP)",
        "units":    "dB",
        "vmin":      0.0,
        "vmax":     70.0,
        "colormap": "nws_cfp",
    },
}

# default product shown when the archive session opens.
DEFAULT_L2_PRODUCT = "reflectivity"


class Level2RadarScan(RadarScan):
    """
    A RadarScan produced from a NEXRAD Level-2 archive file.

    Gates are placed on the WGS84 ellipsoid (true geodesic range and azimuth
    from the radar) rather than the sphere used for Level 3.

    Extra attributes
    ----------------
    tilt_deg : float
        Elevation angle of this sweep in degrees.
    available_tilts : list[float]
        All elevation angles present in the parent Level-2 file (sorted).
    available_products : list[str]
        pyart field names present in the sweep (subset of L2_PRODUCTS keys).
    pyart_field : str
        The pyart field name that was rendered (e.g. "reflectivity").
    """

    geodesy = "wgs84"

    def __init__(
        self,
        site,
        product,       # human-readable label, e.g. "REF"
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
        tilt_deg: float,
        available_tilts: list,
        available_products: list,
        pyart_field: str,
    ):
        super().__init__(
            site=site,
            product=product,
            scan_time=scan_time,
            raw=raw,
            vmin=vmin,
            vmax=vmax,
            units=units,
            colormap=colormap,
            radar_lat=radar_lat,
            radar_lon=radar_lon,
            azimuths_deg=azimuths_deg,
            first_gate_m=first_gate_m,
            gate_width_m=gate_width_m,
        )
        self.tilt_deg = tilt_deg
        self.available_tilts = sorted(available_tilts)
        self.available_products = available_products
        self.pyart_field = pyart_field

    def gate_lonlat(self, az_deg, range_m):
        from pyproj import Geod
        az, rng = np.broadcast_arrays(
            np.asarray(az_deg, dtype=np.float64), np.asarray(range_m, dtype=np.float64)
        )
        lon0 = np.full(az.shape, self.radar_lon)
        lat0 = np.full(az.shape, self.radar_lat)
        lons, lats, _ = Geod(ellps="WGS84").fwd(lon0, lat0, az, rng)
        return lons, lats

    def polar_from_lonlat(self, lon, lat):
        from pyproj import Geod
        lon, lat = np.broadcast_arrays(
            np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64)
        )
        lon0 = np.full(lon.shape, self.radar_lon)
        lat0 = np.full(lon.shape, self.radar_lat)
        az_deg, _, range_m = Geod(ellps="WGS84").inv(lon0, lat0, lon, lat)
        return range_m, az_deg % 360.0

    @property
    def cache_key(self):
        return super().cache_key + (self.pyart_field, self.tilt_deg)

    @property
    def label(self) -> str:
        meta = L2_PRODUCTS.get(self.pyart_field, {})
        name = meta.get("label", self.pyart_field)
        return f"{self.site} {name} {self.tilt_deg:.1f}° {self.scan_time.strftime('%H:%M')}Z"
