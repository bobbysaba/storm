
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

    def __init__(
        self,
        site,
        product,       # human-readable label, e.g. "REF"
        scan_time,
        data,
        lats,
        lons,
        vmin,
        vmax,
        units,
        colormap,
        tilt_deg: float,
        available_tilts: list,
        available_products: list,
        pyart_field: str,
        az_offset: float = 0.0,
    ):
        super().__init__(
            site=site,
            product=product,
            scan_time=scan_time,
            data=data,
            lats=lats,
            lons=lons,
            vmin=vmin,
            vmax=vmax,
            units=units,
            colormap=colormap,
            az_offset=az_offset,
        )
        self.tilt_deg = tilt_deg
        self.available_tilts = sorted(available_tilts)
        self.available_products = available_products
        self.pyart_field = pyart_field

    @property
    def label(self) -> str:
        meta = L2_PRODUCTS.get(self.pyart_field, {})
        name = meta.get("label", self.pyart_field)
        return f"{self.site} {name} {self.tilt_deg:.1f}° {self.scan_time.strftime('%H:%M')}Z"
