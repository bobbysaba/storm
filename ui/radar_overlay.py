# ui/radar_overlay.py
# renders a RadarScan onto the MapLibre map as a raster image overlay.
# converts the numpy data array to a PNG, encodes it as base64,
# and adds/updates an image source + raster layer in MapLibre GL JS.

import io
import base64
import logging
from time import perf_counter
import numpy as np
from typing import Optional

from PyQt6.QtCore import QObject
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display needed
import matplotlib.cm as mcm
import matplotlib.colors as mcolors
import matplotlib.image as mimg
from scipy.ndimage import map_coordinates

from core.radar_scan import RadarScan

log = logging.getLogger(__name__)

# Output grid resolution for polar→Cartesian reprojection.
# Higher = sharper image, slower render.  Lower = faster, slightly blockier.
#   768 → ~140-260 ms render (M1 Mac, modern laptop)
#   512 → ~80-150 ms render  (M1 Mac, modern laptop)
#   256 → ~20-40 ms render   (older/slower field laptop — recommended minimum)
# Override at launch with:  python main.py --render-grid-size 256
RENDER_GRID_SIZE = 768
ADAPTIVE_RENDER_GRID = True
ADAPTIVE_GRID_STEPS = (128, 192, 256, 320, 384, 512, 640, 768, 1024)
ADAPTIVE_DOWN_MS = 280.0
ADAPTIVE_UP_MS = 130.0
ADAPTIVE_DOWN_SCANS = 2
ADAPTIVE_UP_SCANS = 4


# function to set the render grid size (used on startup)
def set_render_grid_size(n: int) -> None:
    # pylint: disable=global-statement
    global RENDER_GRID_SIZE

    # clamp to 64-1024
    RENDER_GRID_SIZE = max(64, min(1024, n))

    # log it
    log.info("RENDER_GRID_SIZE set to %d", RENDER_GRID_SIZE)


# ── NWS Colormaps ─────────────────────────────────────────────────────────────

def _rgba255(r: int, g: int, b: int, a: int = 255) -> tuple[float, float, float, float]:
    return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)


def _make_nws_ref_cmap():
    """RadarScope-style base reflectivity palette for -32 to 90 dBZ."""
    vmin = -32.0
    vmax = 90.0
    stops = [
        (-32.0, _rgba255(0, 0, 0, 0)),
        (0.0,   _rgba255(139, 147, 146, 0)),
        (5.0,   _rgba255(92, 109, 137, 70)),
        (10.0,  _rgba255(57, 85, 134, 141)),
        (15.0,  _rgba255(76, 125, 156, 212)),
        (20.0,  _rgba255(60, 173, 110, 255)),
        (25.0,  _rgba255(21, 125, 30, 255)),
        (30.0,  _rgba255(155, 191, 3, 255)),
        (35.0,  _rgba255(230, 223, 0, 255)),
        (40.0,  _rgba255(250, 148, 0, 255)),
        (45.0,  _rgba255(212, 116, 6, 255)),
        (50.0,  _rgba255(249, 35, 11, 255)),
        (55.0,  _rgba255(186, 37, 22, 255)),
        (60.0,  _rgba255(202, 153, 180, 255)),
        (65.0,  _rgba255(197, 87, 145, 255)),
        (70.0,  _rgba255(154, 36, 224, 255)),
        (75.0,  _rgba255(105, 24, 179, 255)),
        (80.0,  _rgba255(132, 253, 255, 255)),
        (85.0,  _rgba255(98, 181, 196, 255)),
        (90.0,  _rgba255(161, 101, 73, 255)),
        (94.5,  _rgba255(115, 10, 1, 255)),
    ]
    colors = [
        (max(0.0, min(1.0, (value - vmin) / (vmax - vmin))), rgba)
        for value, rgba in stops
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_ref",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    cmap.set_over(_rgba255(115, 10, 1, 255))
    return cmap


def _make_nws_vel_cmap():
    """GR2Analyst velocity colormap with very smooth interpolation.
    
    Based on GR2Analyst BV color table with densely interpolated stops
    for ultra-smooth gradients while preserving exact colors at key breakpoints.
    - Units: MPH, scale: 2.237, step: 10
    - Range: -120 to +120 MPH (approximately -54 to +54 m/s or -105 to +105 kt)
    """
    # GR2Analyst color table with densely interpolated intermediate values
    # Format: (MPH_value, (R, G, B, A))
    stops = [
        (-120.0, (0.000, 0.000, 1.000, 1.00)),  # blue (strong inbound) - GR2 key
        (-105.0, (0.070, 0.235, 0.985, 1.00)),  # interpolated
        (-90.0,  (0.139, 0.471, 0.971, 1.00)),  # interpolated
        (-75.0,  (0.209, 0.706, 0.956, 1.00)),  # interpolated
        (-65.0,  (0.244, 0.824, 0.949, 1.00)),  # interpolated
        (-58.0,  (0.278, 0.941, 0.941, 1.00)),  # cyan - GR2 key
        (-56.0,  (0.289, 0.948, 0.793, 1.00)),  # interpolated
        (-54.0,  (0.300, 0.955, 0.645, 1.00)),  # interpolated
        (-52.0,  (0.311, 0.962, 0.497, 1.00)),  # interpolated
        (-50.0,  (0.322, 0.969, 0.349, 1.00)),  # light green - GR2 key
        (-47.5,  (0.241, 0.977, 0.262, 1.00)),  # interpolated
        (-45.0,  (0.161, 0.985, 0.175, 1.00)),  # interpolated
        (-42.5,  (0.080, 0.992, 0.087, 1.00)),  # interpolated
        (-40.0,  (0.000, 1.000, 0.000, 1.00)),  # green - GR2 key
        (-35.0,  (0.021, 0.900, 0.021, 1.00)),  # interpolated
        (-30.0,  (0.032, 0.800, 0.032, 1.00)),  # interpolated
        (-25.0,  (0.042, 0.688, 0.042, 1.00)),  # interpolated
        (-20.0,  (0.047, 0.588, 0.047, 1.00)),  # interpolated
        (-15.0,  (0.055, 0.482, 0.055, 1.00)),  # interpolated
        (-10.0,  (0.063, 0.376, 0.063, 1.00)),  # dark green - GR2 key
        (-7.5,   (0.157, 0.408, 0.157, 1.00)),  # interpolated
        (-5.0,   (0.251, 0.439, 0.251, 1.00)),  # interpolated
        (-2.5,   (0.345, 0.471, 0.345, 1.00)),  # interpolated
        (-0.01,  (0.439, 0.502, 0.439, 1.00)),  # gray-green - GR2 key
        (0.0,    (0.565, 0.502, 0.565, 1.00)),  # gray near zero - GR2 key
        (2.5,    (0.533, 0.376, 0.423, 1.00)),  # interpolated
        (5.0,    (0.502, 0.251, 0.282, 1.00)),  # interpolated
        (7.5,    (0.471, 0.125, 0.141, 1.00)),  # interpolated
        (10.0,   (0.439, 0.000, 0.000, 1.00)),  # dark red - GR2 key
        (15.0,   (0.549, 0.000, 0.000, 1.00)),  # interpolated
        (20.0,   (0.659, 0.000, 0.000, 1.00)),  # interpolated
        (25.0,   (0.769, 0.000, 0.000, 1.00)),  # interpolated
        (30.0,   (0.878, 0.000, 0.000, 1.00)),  # interpolated
        (35.0,   (0.939, 0.000, 0.000, 1.00)),  # interpolated
        (40.0,   (1.000, 0.000, 0.000, 1.00)),  # red - GR2 key
        (42.5,   (1.000, 0.054, 0.026, 1.00)),  # interpolated
        (45.0,   (1.000, 0.108, 0.051, 1.00)),  # interpolated
        (47.5,   (1.000, 0.162, 0.077, 1.00)),  # interpolated
        (50.0,   (1.000, 0.216, 0.102, 1.00)),  # red-orange - GR2 key
        (52.0,   (0.999, 0.313, 0.115, 1.00)),  # interpolated
        (54.0,   (0.998, 0.410, 0.128, 1.00)),  # interpolated
        (56.0,   (0.997, 0.507, 0.141, 1.00)),  # interpolated
        (58.0,   (0.996, 0.604, 0.153, 1.00)),  # orange - GR2 key
        (61.0,   (0.997, 0.703, 0.115, 1.00)),  # interpolated
        (64.0,   (0.998, 0.802, 0.077, 1.00)),  # interpolated
        (67.0,   (0.999, 0.901, 0.038, 1.00)),  # interpolated
        (70.0,   (1.000, 1.000, 0.000, 1.00)),  # yellow - GR2 key
        (77.5,   (0.950, 0.922, 0.046, 1.00)),  # interpolated
        (85.0,   (0.900, 0.843, 0.092, 1.00)),  # interpolated
        (92.5,   (0.850, 0.765, 0.137, 1.00)),  # interpolated
        (100.0,  (0.800, 0.686, 0.183, 1.00)),  # interpolated
        (107.5,  (0.753, 0.608, 0.206, 1.00)),  # interpolated
        (110.0,  (0.722, 0.549, 0.225, 1.00)),  # interpolated
        (115.0,  (0.682, 0.408, 0.246, 1.00)),  # interpolated
        (120.0,  (0.643, 0.267, 0.267, 1.00)),  # brown (strong outbound) - GR2 key
    ]
    
    # Normalize to 0-1 range for colormap
    vmin, vmax = -120.0, 120.0
    colors = [
        ((value - vmin) / (vmax - vmin), rgba)
        for value, rgba in stops
    ]
    
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_vel",
        colors,
        N=1024  # very high resolution for ultra-smooth gradients
    )
    cmap.set_under(alpha=0)
    cmap.set_over(alpha=0)
    return cmap


def _make_nws_cc_cmap():
    """Correlation coefficient colormap (low=transparent, high=blue/white)."""
    colors = [
        (0.00, (1.000, 1.000, 1.000, 1.00)),  # white
        (0.45, (0.000, 0.000, 0.000, 1.00)),  # black
        (0.60, (0.039, 0.039, 0.745, 1.00)),  # blue
        (0.75, (0.471, 0.471, 1.000, 1.00)),  # light blue
        (0.80, (0.373, 0.961, 0.392, 1.00)),  # green
        (0.85, (0.529, 0.843, 0.039, 1.00)),  # yellow-green
        (0.90, (1.000, 1.000, 0.000, 1.00)),  # yellow
        (0.95, (1.000, 0.549, 0.000, 1.00)),  # orange
        (0.97, (0.882, 0.012, 0.000, 1.00)),  # red
        (0.99, (0.545, 0.118, 0.302, 1.00)),  # dark magenta
        (1.00, (1.000, 0.706, 0.843, 1.00)),  # pink
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_cc",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_zdr_cmap():
    """NWS-style differential reflectivity colormap (-4 to +8 dB)."""
    colors = [
        (0.00, (0.20, 0.20, 0.80, 1.00)),   # blue (negative ZDR)
        (0.25, (0.40, 0.70, 1.00, 1.00)),   # light blue
        (0.33, (0.90, 0.90, 0.90, 0.50)),   # gray (near 0)
        (0.45, (0.20, 0.80, 0.20, 1.00)),   # green
        (0.60, (1.00, 1.00, 0.00, 1.00)),   # yellow
        (0.75, (1.00, 0.50, 0.00, 1.00)),   # orange
        (1.00, (1.00, 0.00, 0.00, 1.00)),   # red (high positive ZDR)
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_zdr",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_sw_cmap():
    """Spectrum width colormap (0–30 kt)."""
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),   # transparent / 0
        (0.10, (0.20, 0.20, 0.60, 0.70)),   # dark blue
        (0.30, (0.20, 0.60, 1.00, 1.00)),   # blue
        (0.50, (0.00, 0.90, 0.90, 1.00)),   # cyan
        (0.70, (0.00, 0.80, 0.00, 1.00)),   # green
        (0.85, (1.00, 1.00, 0.00, 1.00)),   # yellow
        (1.00, (1.00, 0.00, 0.00, 1.00)),   # red
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_sw",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_phi_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.10, 0.20, 0.70, 0.90)),
        (0.35, (0.20, 0.70, 1.00, 1.00)),
        (0.55, (0.00, 0.85, 0.40, 1.00)),
        (0.75, (1.00, 0.90, 0.00, 1.00)),
        (1.00, (1.00, 0.20, 0.00, 1.00)),
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_phi",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_kdp_cmap():
    """AWIPS-style KDP colormap for -2 to +10 deg/km."""
    colors = [
        (0.00, (0.502, 0.000, 0.502, 1.00)),  # -2.0: purple
        (0.17, (0.000, 0.000, 1.000, 1.00)),  # -0.5: blue
        (0.21, (0.000, 1.000, 1.000, 1.00)),  #  0.0: cyan
        (0.25, (0.000, 0.804, 0.000, 1.00)),  #  0.5: green
        (0.29, (0.000, 0.502, 0.000, 1.00)),  #  1.0: dark green
        (0.33, (1.000, 1.000, 0.000, 1.00)),  #  1.5: yellow
        (0.42, (1.000, 0.647, 0.000, 1.00)),  #  2.5: orange
        (0.50, (1.000, 0.000, 0.000, 1.00)),  #  3.5: red
        (0.58, (0.545, 0.000, 0.000, 1.00)),  #  4.5: dark red
        (0.67, (1.000, 0.753, 0.796, 1.00)),  #  5.5: pink
        (0.75, (0.502, 0.000, 0.502, 1.00)),  #  6.5: purple
        (0.83, (1.000, 1.000, 1.000, 1.00)),  #  7.5: white
        (1.00, (0.663, 0.663, 0.663, 1.00)),  # 10.0: gray
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_kdp",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_cfp_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.15, 0.15, 0.18, 0.35)),
        (0.35, (0.28, 0.28, 0.34, 0.60)),
        (0.55, (0.55, 0.55, 0.62, 0.80)),
        (0.75, (0.82, 0.82, 0.86, 0.95)),
        (1.00, (1.00, 1.00, 1.00, 1.00)),
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_cfp",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


NWS_REF_CMAP = _make_nws_ref_cmap()
NWS_VEL_CMAP = _make_nws_vel_cmap()
NWS_CC_CMAP  = _make_nws_cc_cmap()
NWS_ZDR_CMAP = _make_nws_zdr_cmap()
NWS_SW_CMAP  = _make_nws_sw_cmap()
NWS_PHI_CMAP = _make_nws_phi_cmap()
NWS_KDP_CMAP = _make_nws_kdp_cmap()
NWS_CFP_CMAP = _make_nws_cfp_cmap()

COLORMAPS = {
    "nws_ref": NWS_REF_CMAP,
    "nws_vel": NWS_VEL_CMAP,
    "nws_cc":  NWS_CC_CMAP,
    "nws_zdr": NWS_ZDR_CMAP,
    "nws_sw":  NWS_SW_CMAP,
    "nws_phi": NWS_PHI_CMAP,
    "nws_kdp": NWS_KDP_CMAP,
    "nws_cfp": NWS_CFP_CMAP,
}


# ── Standalone render function (thread-safe, no instance state) ───────────────

def _sample_scan_to_grid(
    sample_scan: RadarScan,
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
) -> np.ndarray:
    """Sample a scan onto an arbitrary lat/lon grid using nearest-neighbor."""
    num_az, num_rng = sample_scan.data.shape

    radar_lat = float(sample_scan.lats[:, 0].mean())
    radar_lon = float(sample_scan.lons[:, 0].mean())
    cos_lat = np.cos(np.deg2rad(radar_lat))

    dlat = lat_grid - radar_lat
    dlon = (lon_grid - radar_lon) * cos_lat
    range_km = np.sqrt(dlat ** 2 + dlon ** 2) * 111.32
    az_deg = np.degrees(np.arctan2(dlon, dlat)) % 360.0

    max_range_km = float(np.nanmax(
        np.sqrt(((sample_scan.lats - radar_lat) ** 2 +
                 ((sample_scan.lons - radar_lon) * cos_lat) ** 2)) * 111.32
    ))

    az_idx = ((az_deg - sample_scan.az_offset) % 360.0) * num_az / 360.0
    rng_idx = range_km / max_range_km * (num_rng - 1)
    outside = range_km > max_range_km

    sentinel = sample_scan.vmin - 999.0
    data_filled = np.where(np.isnan(sample_scan.data), sentinel, sample_scan.data)
    coords = np.array([az_idx.ravel(), rng_idx.ravel()])
    sampled = map_coordinates(
        data_filled, coords, order=0, prefilter=False, mode="constant", cval=sentinel
    ).reshape(lat_grid.shape)
    sampled[outside | (sampled <= sentinel + 1.0)] = np.nan
    return sampled


def render_scan_to_png(
    scan: RadarScan,
    grid_size: int,
    mask_scan: RadarScan | None = None,
) -> tuple[bytes, list, float]:
    """
    Convert a RadarScan to a PNG.  Fully thread-safe — takes all inputs
    as parameters and creates its own ScalarMappable; never touches shared state.

    Returns:
        (png_bytes, [west, south, east, north], elapsed_ms)
    """
    IMG = grid_size
    t0 = perf_counter()

    radar_lat = float(scan.lats[:, 0].mean())
    radar_lon = float(scan.lons[:, 0].mean())

    lat_min = float(np.nanmin(scan.lats))
    lat_max = float(np.nanmax(scan.lats))
    lon_min = float(np.nanmin(scan.lons))
    lon_max = float(np.nanmax(scan.lons))

    out_lats = np.linspace(lat_max, lat_min, IMG)
    out_lons = np.linspace(lon_min, lon_max, IMG)
    lon_grid, lat_grid = np.meshgrid(out_lons, out_lats)

    cos_lat = np.cos(np.deg2rad(radar_lat))
    dlat = lat_grid - radar_lat
    dlon = (lon_grid - radar_lon) * cos_lat
    range_km = np.sqrt(dlat ** 2 + dlon ** 2) * 111.32

    data_out = _sample_scan_to_grid(scan, lat_grid, lon_grid)
    if scan.colormap == "nws_ref":
        data_out[~np.isnan(data_out) & (data_out < 8.0)] = np.nan
    elif scan.colormap in ("nws_vel", "nws_cc", "nws_kdp") and mask_scan is not None:
        ref_out = _sample_scan_to_grid(mask_scan, lat_grid, lon_grid)
        data_out[np.isnan(ref_out) | (ref_out < 8.0)] = np.nan

    cmap   = COLORMAPS.get(scan.colormap, NWS_REF_CMAP)
    norm   = mcolors.Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
    mapper = mcm.ScalarMappable(norm=norm, cmap=cmap)

    rgba = mapper.to_rgba(data_out, bytes=True)
    rgba[np.isnan(data_out), 3] = 0

    buf = io.BytesIO()
    mimg.imsave(buf, rgba, format="png")
    png_bytes = buf.getvalue()

    elapsed_ms = (perf_counter() - t0) * 1000.0
    log.debug(
        "render_scan_to_png: %.0f KB PNG in %.1f ms (grid=%d)",
        len(png_bytes) / 1024,
        elapsed_ms,
        IMG,
    )

    bounds = [lon_min, lat_min, lon_max, lat_max]
    return png_bytes, bounds, elapsed_ms


# ── Radar Overlay ─────────────────────────────────────────────────────────────

class RadarOverlay(QObject):
    """
    Manages the radar image overlay on the MapLibre map.

    Works by:
    1. Converting RadarScan data → RGBA PNG via matplotlib
    2. Encoding PNG as base64
    3. Injecting into MapLibre as an image source with known lat/lon bounds
    4. Adding a raster layer that displays the image

    The overlay is updated in-place when new scans arrive.
    """

    LAYER_ID  = "radar-overlay"
    SOURCE_ID = "radar-image"

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map = map_widget
        self._active = False
        self._hidden = False
        self._current_scan: Optional[RadarScan] = None
        self._grid_size = int(RENDER_GRID_SIZE)
        self._adaptive_grid = ADAPTIVE_RENDER_GRID
        self._fast_render_streak = 0
        self._slow_render_streak = 0
        # cache ScalarMappable objects keyed by (colormap, vmin, vmax)
        # avoids recreating norm+cmap on every render call
        _MAPPER_CACHE_MAX = 32
        self._mapper_cache_max = _MAPPER_CACHE_MAX
        self._mapper_cache: dict[tuple, mcm.ScalarMappable] = {}

    def update(self, scan: RadarScan, mask_scan: RadarScan | None = None):
        """render and display a new radar scan (synchronous, for loop playback)."""
        self._current_scan = scan

        try:
            png_bytes, bounds = self._render_to_png(scan, mask_scan=mask_scan)
        except Exception as e:
            log.error("[RadarOverlay] render failed: %s", e, exc_info=True)
            return

        self.inject(png_bytes, bounds)
        log.info("[RadarOverlay] updated with %s (grid=%d)", scan.label, self._grid_size)

    def clear(self):
        """remove the radar overlay from the map."""
        self._map.run_js(f"""
          if (map.getLayer("{self.LAYER_ID}")) map.removeLayer("{self.LAYER_ID}");
          if (map.getSource("{self.SOURCE_ID}")) map.removeSource("{self.SOURCE_ID}");
        """)
        self._active = False
        self._hidden = False
        self._current_scan = None

    def hide(self):
        """Hide the overlay without removing the MapLibre source/layer.

        Much lighter than clear() — avoids the expensive removeLayer + addLayer
        cycle that forces MapLibre to tear down and rebuild its raster rendering
        pipeline.  The next inject() call will just use updateImage() (fast path).
        """
        if self._active:
            self._map.run_js(
                f'if(map.getLayer("{self.LAYER_ID}")) '
                f'map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0);'
            )
        self._hidden = True
        self._current_scan = None

    def inject(self, png_bytes: bytes, bounds: list):
        """Inject a pre-rendered PNG (from background thread) into the map.
        Must be called from the main thread.

        Stores the PNG in the StormSchemeHandler and passes a short URL to
        MapLibre instead of embedding the full base64 blob in runJavaScript.
        This keeps the IPC message tiny so Chromium's renderer JS thread stays
        free to process mouse/keyboard events during the image fetch.
        """
        import time
        scheme_handler = getattr(self._map, "scheme_handler", None)
        if scheme_handler is not None:
            scheme_handler.set_radar_png(png_bytes)
            ts = int(time.monotonic() * 1000) & 0xFFFFFF  # cache-bust token
            image_url = f"storm://app/radar/overlay.png?t={ts}"
        else:
            # SAFE_MAP_MODE — no scheme handler; fall back to data URL
            image_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        restoring = self._hidden
        self._hidden = False
        self._inject_into_map(image_url, bounds, restore_opacity=restoring)
        self._active = True

    # ── Internal ──────────────────────────────────────────────────────────────

    def _render_to_png(
        self,
        scan: RadarScan,
        mask_scan: RadarScan | None = None,
    ) -> tuple[bytes, list]:
        """
        Convert scan data to a PNG using proper polar→Cartesian reprojection.

        Returns:
            (png_bytes, [west, south, east, north])
        """
        IMG = self._grid_size   # adaptive configurable — lower = faster render
        t0 = perf_counter()

        log.debug(
            "rendering %s — grid=%dx%d, colormap=%s, vmin=%.1f vmax=%.1f",
            scan.label, IMG, IMG, scan.colormap, scan.vmin, scan.vmax
        )

        # radar center is at range=0 for all azimuths
        radar_lat = float(scan.lats[:, 0].mean())
        radar_lon = float(scan.lons[:, 0].mean())

        # geographic bounding box
        lat_min = float(np.nanmin(scan.lats))
        lat_max = float(np.nanmax(scan.lats))
        lon_min = float(np.nanmin(scan.lons))
        lon_max = float(np.nanmax(scan.lons))

        # build a square output grid in lat/lon space (north→south, west→east)
        out_lats = np.linspace(lat_max, lat_min, IMG)
        out_lons = np.linspace(lon_min, lon_max, IMG)
        lon_grid, lat_grid = np.meshgrid(out_lons, out_lats)

        data_out = _sample_scan_to_grid(scan, lat_grid, lon_grid)
        # for reflectivity only: mask sub-threshold pixels (~8 dBZ matches RadarScope)
        # do NOT apply to velocity — velocity values include negatives (-100 to +100 kt)
        if scan.colormap == "nws_ref":
            data_out[~np.isnan(data_out) & (data_out < 8.0)] = np.nan
        elif scan.colormap in ("nws_vel", "nws_cc", "nws_kdp") and mask_scan is not None:
            ref_out = _sample_scan_to_grid(mask_scan, lat_grid, lon_grid)
            data_out[np.isnan(ref_out) | (ref_out < 8.0)] = np.nan

        # reuse cached ScalarMappable — recreating norm+cmap every frame is wasteful
        cache_key = (scan.colormap, scan.vmin, scan.vmax)
        if cache_key not in self._mapper_cache:
            if len(self._mapper_cache) >= self._mapper_cache_max:
                self._mapper_cache.pop(next(iter(self._mapper_cache)))
            cmap = COLORMAPS.get(scan.colormap, NWS_REF_CMAP)
            norm = mcolors.Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
            self._mapper_cache[cache_key] = mcm.ScalarMappable(norm=norm, cmap=cmap)
            log.debug("created new ScalarMappable for key %s", cache_key)
        mapper = self._mapper_cache[cache_key]

        rgba = mapper.to_rgba(data_out, bytes=True)   # (IMG, IMG, 4) uint8
        rgba[np.isnan(data_out), 3] = 0               # transparent for no-data pixels

        # encode as PNG
        buf = io.BytesIO()
        mimg.imsave(buf, rgba, format="png")
        png_bytes = buf.getvalue()

        elapsed_ms = (perf_counter() - t0) * 1000.0
        self._maybe_adjust_grid(elapsed_ms)
        log.debug(
            "render complete: %.0f KB PNG in %.1f ms (grid=%d)",
            len(png_bytes) / 1024,
            elapsed_ms,
            IMG,
        )

        bounds = [lon_min, lat_min, lon_max, lat_max]
        return png_bytes, bounds

    def _maybe_adjust_grid(self, elapsed_ms: float) -> None:
        if not self._adaptive_grid:
            return

        steps = ADAPTIVE_GRID_STEPS
        if self._grid_size not in steps:
            self._grid_size = min(steps, key=lambda s: abs(s - self._grid_size))

        idx = steps.index(self._grid_size)
        changed = False
        prev = self._grid_size

        if elapsed_ms > ADAPTIVE_DOWN_MS:
            self._slow_render_streak += 1
            self._fast_render_streak = 0
            if self._slow_render_streak >= ADAPTIVE_DOWN_SCANS and idx > 0:
                self._grid_size = steps[idx - 1]
                self._slow_render_streak = 0
                changed = True
        elif elapsed_ms < ADAPTIVE_UP_MS:
            self._fast_render_streak += 1
            self._slow_render_streak = 0
            if self._fast_render_streak >= ADAPTIVE_UP_SCANS and idx < len(steps) - 1:
                self._grid_size = steps[idx + 1]
                self._fast_render_streak = 0
                changed = True
        else:
            self._fast_render_streak = 0
            self._slow_render_streak = 0

        if changed:
            log.info(
                "[RadarOverlay] adaptive grid %d -> %d (render=%.1f ms, down>%dms up<%dms)",
                prev,
                self._grid_size,
                elapsed_ms,
                int(ADAPTIVE_DOWN_MS),
                int(ADAPTIVE_UP_MS),
            )

    def _inject_into_map(self, image_url: str, bounds: list, restore_opacity: bool = False):
        """
        Add or update the radar image source and layer in MapLibre.

        image_url may be a storm://app/radar/overlay.png?t=... URL (preferred —
        small IPC message, browser fetches PNG asynchronously so the renderer's
        JS thread stays free for input events) or a data: URL fallback.

        MapLibre image sources expect coordinates as:
          [[NW_lon, NW_lat], [NE_lon, NE_lat], [SE_lon, SE_lat], [SW_lon, SW_lat]]
        """
        west, south, east, north = bounds

        coords_js = (
            f"[[{west},{north}], [{east},{north}], [{east},{south}], [{west},{south}]]"
        )

        log.debug("injecting radar image into map (bounds %s)", bounds)

        js = f"""
        (function() {{
          const imageUrl = "{image_url}";
          const coords   = {coords_js};

          try {{
            if (map.getSource("{self.SOURCE_ID}")) {{
              // update existing source in-place — avoids layer flicker
              map.getSource("{self.SOURCE_ID}").updateImage({{
                url: imageUrl,
                coordinates: coords
              }});
              {"" if not restore_opacity else f'''
              // restore opacity after hide() set it to 0 for site switch;
              // disable fade so the new site's data appears instantly
              if (map.getLayer("{self.LAYER_ID}")) {{
                map.setPaintProperty("{self.LAYER_ID}", "raster-fade-duration", 0);
                map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0.75);
                // re-enable fade for subsequent updates (loop playback, live updates)
                setTimeout(function() {{
                  if (map.getLayer("{self.LAYER_ID}"))
                    map.setPaintProperty("{self.LAYER_ID}", "raster-fade-duration", 300);
                }}, 500);
              }}'''}
            }} else {{
              // first time — add source and layer
              map.addSource("{self.SOURCE_ID}", {{
                type: "image",
                url: imageUrl,
                coordinates: coords
              }});

              try {{
                map.addLayer({{
                  id: "{self.LAYER_ID}",
                  type: "raster",
                  source: "{self.SOURCE_ID}",
                  paint: {{
                    "raster-opacity": 0.75,
                    "raster-fade-duration": 300
                  }}
                }}, "road-unpaved");   // insert below road labels/roads so they stay visible
              }} catch(layerErr) {{
                // fallback: "road-unpaved" may not exist yet — add without beforeId
                console.warn("[STORM] radar addLayer beforeId failed, adding on top:", layerErr.message);
                map.addLayer({{
                  id: "{self.LAYER_ID}",
                  type: "raster",
                  source: "{self.SOURCE_ID}",
                  paint: {{
                    "raster-opacity": 0.75,
                    "raster-fade-duration": 300
                  }}
                }});
              }}
            }}
          }} catch(e) {{
            console.error("[STORM] radar inject error:", e.message || e);
          }}
        }})();
        """
        self._map.run_js(js)
