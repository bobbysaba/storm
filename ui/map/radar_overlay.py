
import io
import base64
import logging
import math
import threading
from collections import OrderedDict
from time import perf_counter
import numpy as np
from typing import Optional

from functools import lru_cache
from PyQt6.QtCore import QObject

from core import perf_log
from core.radar_scan import RadarScan

log = logging.getLogger(__name__)


# matplotlib costs ~0.4 s to import, so it loads on the first render, not at startup
def _mpl_colors():
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")   # non-interactive backend — no display needed
    import matplotlib.colors as mcolors  # noqa: PLC0415
    return mcolors

# output grid resolution for polar→Cartesian reprojection.
RENDER_GRID_SIZE = 768
MAX_RENDER_GRID_SIZE = 1536
ADAPTIVE_RENDER_GRID = True
ADAPTIVE_GRID_STEPS = (128, 192, 256, 320, 384, 512, 640, 768, 1024, 1280, 1536)
ADAPTIVE_DOWN_MS = 280.0
ADAPTIVE_UP_MS = 130.0
ADAPTIVE_DOWN_SCANS = 2
ADAPTIVE_UP_SCANS = 4

# Archive mode's sharp render of a paused frame (main_window_archive.py).
# Kept apart from the sizes above, which live mode's adaptive grid owns.
# Measured on an M2 (KTLX 2013-05-31 23:23, 0.5 deg): 4096 px 1.8 s, 3072 px
# 1.0 s, 2048 px 0.48 s. A slower computer steps down a size when one render
# takes more than ARCHIVE_SUPERRES_SLOW_S of CPU.
ARCHIVE_SUPERRES_SIZES = (4096, 3072, 2048)
ARCHIVE_SUPERRES_GRID_SIZE = ARCHIVE_SUPERRES_SIZES[0]
# the sharp render covers every gate out to this range (the far edge of
# reflectivity), so pausing doesn't cut the outer echoes off; at 35 N that's
# ~230 m/px on the ground at 4096 px, near the 250 m super-res gate spacing
ARCHIVE_SUPERRES_RANGE_M = 460_000.0
ARCHIVE_SUPERRES_SLOW_S = 2.5
# a sharp render is skipped while less system memory than this is free; a
# 4096 px one needs about 300 MB at its peak (index, RGBA, strips, PNG)
ARCHIVE_SUPERRES_MIN_FREE_MB = 1024.0


def next_superres_size(size: int, seconds: float) -> int:
    """The size to use after a render of `size` took `seconds` of CPU."""
    if seconds <= ARCHIVE_SUPERRES_SLOW_S:
        return size
    smaller = [s for s in ARCHIVE_SUPERRES_SIZES if s < size]
    return smaller[0] if smaller else size


def sharp_render_memory_ok() -> bool:
    """True unless the system is known to be short of free memory.

    Checked before each sharp render, so a busy moment skips one render and
    the next pause tries again. Unknown (macOS) counts as enough.
    """
    from core.mem_probe import system_memory_mb  # noqa: PLC0415
    _total, available = system_memory_mb()
    return available < 0 or available >= ARCHIVE_SUPERRES_MIN_FREE_MB


# function to set the render grid size (used on startup)
def set_render_grid_size(n: int) -> None:
    # pylint: disable=global-statement
    global RENDER_GRID_SIZE

    # clamp to a practical range; higher values are expensive for PNG rendering.
    RENDER_GRID_SIZE = max(64, min(MAX_RENDER_GRID_SIZE, n))

    # log it
    log.info("RENDER_GRID_SIZE set to %d", RENDER_GRID_SIZE)


def apply_lite_mode() -> None:
    """Cap the render grid for weak hardware. Call before MainWindow is created."""
    # pylint: disable=global-statement
    global RENDER_GRID_SIZE, MAX_RENDER_GRID_SIZE, ADAPTIVE_GRID_STEPS
    import runtime_flags  # noqa: PLC0415

    MAX_RENDER_GRID_SIZE = runtime_flags.LITE_RENDER_GRID_MAX
    ADAPTIVE_GRID_STEPS = tuple(s for s in ADAPTIVE_GRID_STEPS if s <= MAX_RENDER_GRID_SIZE)
    RENDER_GRID_SIZE = min(RENDER_GRID_SIZE, runtime_flags.LITE_RENDER_GRID_START)
    log.info("lite mode: radar grid starts at %d, max %d", RENDER_GRID_SIZE, MAX_RENDER_GRID_SIZE)


def set_adaptive_render_grid(enabled: bool) -> None:
    """Enable or disable adaptive resolution scaling. Call before MainWindow is created."""
    # pylint: disable=global-statement
    global ADAPTIVE_RENDER_GRID
    ADAPTIVE_RENDER_GRID = enabled
    log.info("ADAPTIVE_RENDER_GRID set to %s", enabled)



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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
        "nws_ref",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    cmap.set_over(_rgba255(115, 10, 1, 255))
    return cmap


def _make_nws_vel_cmap():
    """RadarScope-like base velocity palette with a neutral zero center."""
    stops = [
        (-75.0, (0.020, 0.078, 0.820, 1.00)),  # deep blue
        (-60.0, (0.090, 0.365, 0.980, 1.00)),  # royal blue
        (-45.0, (0.145, 0.765, 0.980, 1.00)),  # cyan
        (-32.0, (0.000, 0.930, 0.780, 1.00)),  # aqua
        (-22.0, (0.000, 0.820, 0.220, 1.00)),  # green
        (-12.0, (0.000, 0.540, 0.000, 1.00)),  # dark green
        (-6.0,  (0.220, 0.420, 0.220, 1.00)),  # muted green
        (-2.0,  (0.420, 0.480, 0.420, 1.00)),  # near-zero inbound tint
        (0.0,   (0.600, 0.600, 0.600, 1.00)),  # neutral gray
        (2.0,   (0.480, 0.380, 0.380, 1.00)),  # near-zero outbound tint
        (6.0,   (0.520, 0.140, 0.140, 1.00)),  # dark red
        (12.0,  (0.730, 0.000, 0.000, 1.00)),  # red
        (22.0,  (0.950, 0.000, 0.000, 1.00)),  # bright red
        (32.0,  (1.000, 0.310, 0.000, 1.00)),  # orange-red
        (45.0,  (1.000, 0.620, 0.000, 1.00)),  # orange
        (60.0,  (1.000, 0.950, 0.000, 1.00)),  # yellow
        (75.0,  (1.000, 0.980, 0.820, 1.00)),  # pale yellow
    ]

    vmin, vmax = -75.0, 75.0
    colors = [
        ((value - vmin) / (vmax - vmin), rgba)
        for value, rgba in stops
    ]

    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
        "nws_vel",
        colors,
        N=1024
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
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
    cmap = _mpl_colors().LinearSegmentedColormap.from_list(
        "nws_cfp",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


# built on first use (see _mpl_colors)
@lru_cache(maxsize=1)
def _colormaps() -> dict:
    return {
        "nws_ref": _make_nws_ref_cmap(),
        "nws_vel": _make_nws_vel_cmap(),
        "nws_cc":  _make_nws_cc_cmap(),
        "nws_zdr": _make_nws_zdr_cmap(),
        "nws_sw":  _make_nws_sw_cmap(),
        "nws_phi": _make_nws_phi_cmap(),
        "nws_kdp": _make_nws_kdp_cmap(),
        "nws_cfp": _make_nws_cfp_cmap(),
    }


_MERC_R = 6378137.0
_MERC_LAT_LIMIT = 85.05112878

# azimuth resolution of the cached pixel index (0.01 degree bins)
_AZ_BINS = 36000
# range resolution of the cached pixel index; uint16 bins reach 655 km
_RANGE_BIN_M = 10.0
_RANGE_BINS = 65536
_PIXEL_INDEX_CACHE_MAX = 4
# an index costs 4 bytes a pixel: 67 MB at 4096 px, so only one that size is
# kept beside the normal ones
_SHARP_INDEX_MIN_GRID = 2048
_SHARP_INDEX_CACHE_MAX = 1
_LUT_CACHE_MAX = 32
# pixels per strip when building an index or rendering: a 4096 px frame is
# done 32 rows at a time, so its temporaries stay a few MB instead of most of
# a GB. Larger strips aren't faster, and macOS's allocator keeps the freed
# mid-size blocks (256-row strips left the process ~550 MB bigger).
RENDER_STRIP_PIXELS = 1 << 17
# rendered frames kept for loop playback / scrubbing
_PNG_CACHE_MAX_BYTES = 32 * 1024 * 1024
# reflectivity below this is not drawn (~8 dBZ matches RadarScope)
_MIN_REF_DBZ = 8.0
# products drawn only where the reflectivity scan shows precipitation
_REF_MASKED_COLORMAPS = ("nws_vel", "nws_cc", "nws_kdp")


def _lonlat_to_merc(lon, lat):
    lat_c = np.clip(lat, -_MERC_LAT_LIMIT, _MERC_LAT_LIMIT)
    x = np.deg2rad(lon) * _MERC_R
    y = np.log(np.tan(np.pi / 4 + np.deg2rad(lat_c) / 2)) * _MERC_R
    return x, y


def _merc_to_lonlat(x, y):
    lon = np.rad2deg(x / _MERC_R)
    lat = np.rad2deg(2 * np.arctan(np.exp(y / _MERC_R)) - np.pi / 2)
    return lon, lat


class _PixelIndex:
    """Where each output pixel falls in polar space around one radar.

    Depends only on the radar location, the scan's maximum range and the
    output grid size, so it is shared by every frame (and every product with
    the same reach) from a site.
    """

    __slots__ = ("bounds", "az_bin", "range_bin")

    def __init__(self, bounds, az_bin, range_bin):
        self.bounds = bounds          # [west, south, east, north]
        self.az_bin = az_bin          # uint16, flat (grid*grid)
        self.range_bin = range_bin    # uint16, flat (grid*grid)


_cache_lock = threading.Lock()
_pixel_index_cache: "OrderedDict[tuple, _PixelIndex]" = OrderedDict()
_lut_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_png_cache: "OrderedDict[tuple, tuple[bytes, list]]" = OrderedDict()
_png_cache_bytes = 0


def _far_edge_m(scan: RadarScan) -> float:
    return scan.first_gate_m + (scan.raw.shape[1] - 0.5) * scan.gate_width_m


def _footprint_merc(scan: RadarScan) -> tuple[float, float, float, float]:
    """Web Mercator box (x_min, y_min, x_max, y_max) around the scan's far-edge circle."""
    ring_lon, ring_lat = scan.gate_lonlat(np.arange(0.0, 360.0, 0.1), _far_edge_m(scan))
    ring_x, ring_y = _lonlat_to_merc(ring_lon, ring_lat)
    return float(ring_x.min()), float(ring_y.min()), float(ring_x.max()), float(ring_y.max())


def footprint_bounds(scan: RadarScan) -> list:
    """[west, south, east, north] of the scan's footprint, as the PNG path uses."""
    x_min, y_min, x_max, y_max = _footprint_merc(scan)
    lon_w, lat_s = _merc_to_lonlat(np.array(x_min), np.array(y_min))
    lon_e, lat_n = _merc_to_lonlat(np.array(x_max), np.array(y_max))
    return [float(lon_w), float(lat_s), float(lon_e), float(lat_n)]


def superres_crop_radius_m(scan: RadarScan) -> float:
    """Half-width (Web Mercator m) of the square around the radar holding
    every gate out to ARCHIVE_SUPERRES_RANGE_M. It depends on the station
    alone, so every product and tilt shares one sharp pixel index."""
    ring_lon, ring_lat = scan.gate_lonlat(np.arange(0.0, 360.0, 1.0), ARCHIVE_SUPERRES_RANGE_M)
    ring_x, ring_y = _lonlat_to_merc(ring_lon, ring_lat)
    cx, cy = _lonlat_to_merc(scan.radar_lon, scan.radar_lat)
    return float(max(np.abs(ring_x - cx).max(), np.abs(ring_y - cy).max()))


def _crop_merc(scan: RadarScan, crop_radius_m: float) -> tuple[float, float, float, float]:
    """Web Mercator square of half-width crop_radius_m centered on the radar."""
    cx, cy = _lonlat_to_merc(scan.radar_lon, scan.radar_lat)
    cx, cy = float(cx), float(cy)
    return cx - crop_radius_m, cy - crop_radius_m, cx + crop_radius_m, cy + crop_radius_m


def _trim_pixel_index_cache() -> None:
    """Drop the least recently used indexes over each size class's limit.
    Call with _cache_lock held."""
    sharp = [k for k in _pixel_index_cache if k[4] >= _SHARP_INDEX_MIN_GRID]
    normal = [k for k in _pixel_index_cache if k[4] < _SHARP_INDEX_MIN_GRID]
    for key in sharp[:-_SHARP_INDEX_CACHE_MAX] + normal[:-_PIXEL_INDEX_CACHE_MAX]:
        del _pixel_index_cache[key]


def _pixel_index(scan: RadarScan, grid_size: int, crop_radius_m: float | None = None) -> _PixelIndex:
    """Return the (cached) pixel index for scan's footprint at grid_size.

    The output grid is uniform in Web Mercator so MapLibre's image source
    lines up with the basemap; its extent is the circle at the scan's far edge,
    or with crop_radius_m a square of that half-width around the radar.
    """
    key = (
        scan.geodesy,
        round(scan.radar_lat, 5),
        round(scan.radar_lon, 5),
        # a crop doesn't depend on the scan's reach, so products share it
        round(_far_edge_m(scan), 1) if crop_radius_m is None else None,
        int(grid_size),
        None if crop_radius_m is None else round(float(crop_radius_m), 1),
    )
    with _cache_lock:
        index = _pixel_index_cache.get(key)
        if index is not None:
            _pixel_index_cache.move_to_end(key)
            return index

    if crop_radius_m is None:
        x_min, y_min, x_max, y_max = _footprint_merc(scan)
    else:
        x_min, y_min, x_max, y_max = _crop_merc(scan, crop_radius_m)

    out_x = np.linspace(x_min, x_max, grid_size)
    out_y = np.linspace(y_max, y_min, grid_size)   # rows top→bottom
    az_bin = np.empty(grid_size * grid_size, dtype=np.uint16)
    range_bin = np.empty(grid_size * grid_size, dtype=np.uint16)
    # built a strip of rows at a time: whole-grid float64 temporaries would
    # be over a GB at the sharp sizes
    strip_rows = max(1, RENDER_STRIP_PIXELS // grid_size)
    for r0 in range(0, grid_size, strip_rows):
        r1 = min(grid_size, r0 + strip_rows)
        x_grid, y_grid = np.meshgrid(out_x, out_y[r0:r1])
        lon_grid, lat_grid = _merc_to_lonlat(x_grid, y_grid)
        # polar lookup uses the scan's own earth model, so it is the exact
        # inverse of where the decoder placed the gates
        range_m, az_deg = scan.polar_from_lonlat(lon_grid, lat_grid)
        flat = slice(r0 * grid_size, r1 * grid_size)
        az_bin[flat] = (az_deg.ravel() * (_AZ_BINS / 360.0)).astype(np.int32) % _AZ_BINS
        range_bin[flat] = np.minimum(range_m.ravel() / _RANGE_BIN_M, _RANGE_BINS - 1)

    lon_w, lat_s = _merc_to_lonlat(np.array(x_min), np.array(y_min))
    lon_e, lat_n = _merc_to_lonlat(np.array(x_max), np.array(y_max))
    index = _PixelIndex(
        bounds=[float(lon_w), float(lat_s), float(lon_e), float(lat_n)],
        az_bin=az_bin,
        range_bin=range_bin,
    )
    with _cache_lock:
        _pixel_index_cache[key] = index
        _trim_pixel_index_cache()
    return index


def ensure_pixel_index(scan: RadarScan, grid_size: int, crop_radius_m: float | None = None) -> None:
    """Build (or touch) the pixel index a render will use. A 4096 px one
    takes several seconds the first time, so callers timing the render
    itself build it first."""
    _pixel_index(scan, grid_size, crop_radius_m)


def _range_gate_table(scan: RadarScan) -> np.ndarray:
    """Gate number for each range bin (int32, -1 outside the scan)."""
    num_rng = scan.raw.shape[1]
    centers_m = (np.arange(_RANGE_BINS) + 0.5) * _RANGE_BIN_M
    gates = np.rint((centers_m - scan.first_gate_m) / scan.gate_width_m).astype(np.int32)
    near_edge_m = max(0.0, scan.first_gate_m - 0.5 * scan.gate_width_m)
    gates[(centers_m > _far_edge_m(scan)) | (centers_m < near_edge_m)] = -1
    gates[gates >= num_rng] = -1
    return gates


def _az_row_table(azimuths_deg: np.ndarray) -> np.ndarray:
    """Radial (row) number for each azimuth bin (int32, -1 where no radial).

    Picks the radial whose center is nearest, so radials may start anywhere,
    arrive in any order and be unevenly spaced.  Bins further than one radial
    spacing from any radial (sector scans) are left empty.
    """
    az = np.asarray(azimuths_deg, dtype=np.float64) % 360.0
    num_az = az.size
    order = np.argsort(az, kind="stable")
    az_sorted = az[order]
    centers = (np.arange(_AZ_BINS) + 0.5) * (360.0 / _AZ_BINS)

    pos = np.searchsorted(az_sorted, centers)
    lo = (pos - 1) % num_az
    hi = pos % num_az
    d_lo = (centers - az_sorted[lo]) % 360.0
    d_hi = (az_sorted[hi] - centers) % 360.0
    use_hi = d_hi < d_lo
    rows = np.where(use_hi, order[hi], order[lo]).astype(np.int32)

    if num_az > 1:
        gaps = np.diff(az_sorted, append=az_sorted[0] + 360.0)
        spacing = float(np.median(gaps))
        rows[np.where(use_hi, d_hi, d_lo) > spacing] = -1
    return rows


def _lookup_tables(scan: RadarScan) -> tuple[np.ndarray, np.ndarray]:
    """(gate per range bin, radial per azimuth bin) for scan."""
    return _range_gate_table(scan), _az_row_table(scan.azimuths_deg)


def _sample_scan(
    scan: RadarScan, index: _PixelIndex, tables=None, pixels: slice = slice(None),
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-gate sample of scan.raw for the output pixels in `pixels`
    (all of them by default); tables are _lookup_tables(scan) if already made.

    Returns (samples, invalid): flat arrays, one entry per pixel.  Samples at
    invalid pixels are meaningless.
    """
    gate_table, row_table = tables if tables is not None else _lookup_tables(scan)
    gates = gate_table[index.range_bin[pixels]]
    rows = row_table[index.az_bin[pixels]]
    invalid = (rows < 0) | (gates < 0)
    flat = rows * scan.raw.shape[1] + gates
    flat[invalid] = 0
    return scan.raw.reshape(-1)[flat], invalid


def _sample_scan_values(
    scan: RadarScan, index: _PixelIndex, tables=None, pixels: slice = slice(None),
) -> np.ndarray:
    """Like _sample_scan but as physical float32 values, NaN where invalid."""
    samples, invalid = _sample_scan(scan, index, tables, pixels)
    if scan.value_table is not None:
        values = scan.value_table[samples]
    else:
        values = samples.astype(np.float32)
    values[invalid] = np.nan
    return values


def _to_rgba(scan: RadarScan, values: np.ndarray) -> np.ndarray:
    """Colormap physical values to uint8 RGBA; NaN becomes fully transparent."""
    if scan.colormap == "nws_ref":
        # NaN compares False, so missing gates stay NaN
        values = np.where(values < _MIN_REF_DBZ, np.nan, values)
    import matplotlib.cm as mcm  # noqa: PLC0415
    cmaps = _colormaps()
    cmap = cmaps.get(scan.colormap, cmaps["nws_ref"])
    norm = _mpl_colors().Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
    rgba = mcm.ScalarMappable(norm=norm, cmap=cmap).to_rgba(values, bytes=True)
    rgba[np.isnan(values)] = 0
    return rgba


def _code_lut(scan: RadarScan) -> np.ndarray:
    """256-entry RGBA table for a packed-code scan (cached)."""
    key = (scan.colormap, scan.vmin, scan.vmax, scan.value_table.tobytes())
    with _cache_lock:
        lut = _lut_cache.get(key)
        if lut is not None:
            _lut_cache.move_to_end(key)
            return lut
    lut = _to_rgba(scan, scan.value_table)
    with _cache_lock:
        _lut_cache[key] = lut
        while len(_lut_cache) > _LUT_CACHE_MAX:
            _lut_cache.popitem(last=False)
    return lut


def _mask_applies(scan: RadarScan, mask_scan: RadarScan | None) -> bool:
    """True if mask_scan should gate scan.  The pixel index stores absolute
    range and azimuth, so it serves any scan from the same radar — a mask
    from a different radar cannot be aligned and is ignored."""
    return (
        mask_scan is not None
        and scan.colormap in _REF_MASKED_COLORMAPS
        and abs(mask_scan.radar_lat - scan.radar_lat) < 1e-4
        and abs(mask_scan.radar_lon - scan.radar_lon) < 1e-4
    )


def _png_cache_key(scan: RadarScan, grid_size: int, mask_scan: RadarScan | None) -> tuple:
    masked = _mask_applies(scan, mask_scan)
    return (scan.cache_key, int(grid_size), mask_scan.cache_key if masked else None)


def cached_scan_png(
    scan: RadarScan,
    grid_size: int,
    mask_scan: RadarScan | None = None,
) -> tuple[bytes, list] | None:
    """Return (png_bytes, bounds) if this frame was already rendered, else None."""
    key = _png_cache_key(scan, grid_size, mask_scan)
    with _cache_lock:
        hit = _png_cache.get(key)
        if hit is not None:
            _png_cache.move_to_end(key)
        return hit


def _store_png(key: tuple, png_bytes: bytes, bounds: list) -> None:
    # pylint: disable=global-statement
    global _png_cache_bytes
    with _cache_lock:
        old = _png_cache.pop(key, None)
        if old is not None:
            _png_cache_bytes -= len(old[0])
        _png_cache[key] = (png_bytes, bounds)
        _png_cache_bytes += len(png_bytes)
        while _png_cache_bytes > _PNG_CACHE_MAX_BYTES and len(_png_cache) > 1:
            _, (evicted, _) = _png_cache.popitem(last=False)
            _png_cache_bytes -= len(evicted)


def render_scan_to_png(
    scan: RadarScan,
    grid_size: int,
    mask_scan: RadarScan | None = None,
    crop_radius_m: float | None = None,
) -> tuple[bytes, list, float | None]:
    """
    Convert a RadarScan to a PNG.  Thread-safe.

    Frames are cached, so replaying a loop or scrubbing back to a scan that
    was already drawn costs nothing.

    crop_radius_m: when given, the image covers a square of this half-width
    (Web Mercator meters) around the radar instead of the scan's whole
    footprint. Archive mode's sharp renders use it; they keep their own cache
    (ui/app/radar_superres_cache.py) and stay out of this one.

    Returns:
        (png_bytes, [west, south, east, north], elapsed_ms)
        elapsed_ms is None when the frame came from the cache.
    """
    use_cache = crop_radius_m is None
    cache_key = _png_cache_key(scan, grid_size, mask_scan)
    hit = cached_scan_png(scan, grid_size, mask_scan) if use_cache else None
    if hit is not None:
        return hit[0], hit[1], None

    t0 = perf_counter()
    index = _pixel_index(scan, grid_size, crop_radius_m)

    tables = _lookup_tables(scan)
    lut = _code_lut(scan) if scan.value_table is not None else None
    masked = _mask_applies(scan, mask_scan)
    mask_tables = _lookup_tables(mask_scan) if masked else None
    num_px = grid_size * grid_size
    rgba = np.empty((num_px, 4), dtype=np.uint8)
    step = max(1, RENDER_STRIP_PIXELS // grid_size) * grid_size
    for start in range(0, num_px, step):
        pixels = slice(start, min(num_px, start + step))
        samples, invalid = _sample_scan(scan, index, tables, pixels)
        if lut is not None:
            strip = lut[samples]
        else:
            strip = _to_rgba(scan, samples.astype(np.float32))
        strip[invalid] = 0
        if masked:
            ref = _sample_scan_values(mask_scan, index, mask_tables, pixels)
            # NaN compares False against the threshold, hence the explicit isnan
            strip[np.isnan(ref) | (ref < _MIN_REF_DBZ)] = 0
        rgba[pixels] = strip

    _mpl_colors()   # makes sure the Agg backend is selected
    import matplotlib.image as mimg  # noqa: PLC0415
    buf = io.BytesIO()
    mimg.imsave(buf, rgba.reshape(grid_size, grid_size, 4), format="png")
    png_bytes = buf.getvalue()

    elapsed_ms = (perf_counter() - t0) * 1000.0
    perf_log.event("radar.render_live", size=grid_size, ms=round(elapsed_ms), cropped=not use_cache,
                   product=getattr(scan, "product", None), kb=round(len(png_bytes) / 1024))
    log.debug(
        "render_scan_to_png: %.0f KB PNG in %.1f ms (grid=%d)",
        len(png_bytes) / 1024,
        elapsed_ms,
        grid_size,
    )

    if use_cache:
        _store_png(cache_key, png_bytes, index.bounds)
    return png_bytes, index.bounds, elapsed_ms



class RadarOverlay(QObject):
    """
    Manages the radar image overlay on the MapLibre map.

    Works by:
    1. Converting RadarScan data → RGBA PNG (render_scan_to_png)
    2. Handing the PNG to the storm:// scheme handler (base64 fallback)
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
        self._gl_active = False
        self._fast_render_streak = 0
        self._slow_render_streak = 0

    def update(self, scan: RadarScan, mask_scan: RadarScan | None = None):
        """render and display a new radar scan (synchronous, for loop playback)."""
        self._current_scan = scan

        from ui.map import radar_gl  # noqa: PLC0415
        if radar_gl.enabled() and radar_gl.supports(scan, mask_scan):
            try:
                self.inject_gl(radar_gl.build_payload(scan))
                return
            except Exception as e:
                log.error("[RadarOverlay] GPU payload failed, using PNG: %s", e, exc_info=True)

        try:
            png_bytes, bounds, elapsed_ms = render_scan_to_png(
                scan, self._grid_size, mask_scan=mask_scan
            )
        except Exception as e:
            log.error("[RadarOverlay] render failed: %s", e, exc_info=True)
            return

        self.inject(png_bytes, bounds)
        self._maybe_adjust_grid(elapsed_ms)
        log.info("[RadarOverlay] updated with %s (grid=%d)", scan.label, self._grid_size)

    def clear(self):
        """Visually clear the radar without destroying the MapLibre source (avoids render deadlocks)."""
        self.hide(transient=False)
        self._active = False
        self._hidden = False
        self._current_scan = None

    def hide(self, transient: bool = True):
        """
        Hide the overlay instantly by forcing 0 opacity and pushing a 1x1 transparent PNG.
        """
        self._hidden = True
        self._current_scan = None
        self._hide_gl()

        # always force opacity 0 and inject a 1x1 PNG to clear the GPU pipeline
        js = f"""
        (function() {{
            try {{
                if (typeof map !== 'undefined') {{
                    if (map.getLayer("{self.LAYER_ID}")) {{
                        map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0);
                    }}
                    var src = map.getSource("{self.SOURCE_ID}");
                    if (src && src.updateImage) {{
                        var tinyPng = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
                        var coords = window._radar_last_coords || [[0,0],[0,0],[0,0],[0,0]];
                        src.updateImage({{url: tinyPng, coordinates: coords}});
                    }}
                }}
            }} catch(e) {{ console.error("STORM Hide error:", e); }}
        }})();
        """
        self._map.run_js(js)

        # clear the Python-side scheme handler
        try:
            scheme_handler = getattr(self._map, "scheme_handler", None)
            if scheme_handler is not None:
                import base64
                tiny = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
                scheme_handler.set_radar_png(tiny)
        except Exception:
            pass

        if transient:
            try:
                self._map.run_js("if (typeof window !== 'undefined') window._stormRadarSuspend = true;")
            except Exception:
                pass

    def inject(self, png_bytes: bytes, bounds: list):
        """Inject a pre-rendered PNG into the map."""
        import time
        scheme_handler = getattr(self._map, "scheme_handler", None)
        if scheme_handler is not None:
            scheme_handler.set_radar_png(png_bytes)
            ts = int(time.monotonic() * 1000) & 0xFFFFFF  # cache-bust token
            image_url = f"storm://app/radar/overlay.png?t={ts}"
        else:
            import base64
            image_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        
        restoring = self._hidden
        self._hide_gl()
        self._inject_into_map(image_url, bounds, restore_opacity=restoring)
        self._hidden = False
        self._active = True


    def inject_gl(self, payload: dict):
        """Draw a scan with the WebGL layer (see radar_gl).  The PNG layer is faded out."""
        import json
        import time
        scheme_handler = getattr(self._map, "scheme_handler", None)
        if scheme_handler is None:
            raise RuntimeError("GPU radar needs the storm:// scheme handler")
        scheme_handler.set_radar_gl({
            "data": payload["data_png"],
            "az": payload["az_png"],
            "lut": payload["lut_png"],
        })
        token = int(time.monotonic() * 1000) & 0xFFFFFF
        self._map.run_js(
            f"""
            window._stormRadarSuspend = false;
            if (typeof map !== 'undefined' && map.getLayer("{self.LAYER_ID}")) {{
                map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0);
            }}
            if (window.stormSetRadarGL) window.stormSetRadarGL({json.dumps(payload["meta"])}, {token});
            """
        )
        self._gl_active = True
        self._hidden = False
        self._active = True

    def _hide_gl(self):
        if getattr(self, "_gl_active", False):
            self._map.run_js("if (window.stormHideRadarGL) window.stormHideRadarGL();")
            self._gl_active = False

    def _maybe_adjust_grid(self, elapsed_ms: float | None) -> None:
        # None means the frame came from the render cache — nothing to learn
        if not self._adaptive_grid or elapsed_ms is None:
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
            # already at the smallest grid and still slow: this machine wants lite mode
            if idx == 0 and self._slow_render_streak == ADAPTIVE_DOWN_SCANS:
                log.warning(
                    "[RadarOverlay] radar render still %.0f ms at the smallest grid; "
                    "try starting STORM with --lite",
                    elapsed_ms,
                )
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
        """Add or update the radar image source and layer in MapLibre."""
        west, south, east, north = bounds
        coords_js = f"[[{west},{north}], [{east},{north}], [{east},{south}], [{west},{south}]]"

        if self._hidden and not restore_opacity:
            return

        js = f"""
        (function() {{
          try {{
              const imageUrl = "{image_url}";
              const coords   = {coords_js};
              window._radar_last_coords = coords;

              if (window._stormRadarSuspend && !{str(restore_opacity).lower()}) {{
                  return;
              }}
              window._stormRadarSuspend = false;

              if (typeof map !== 'undefined') {{
                  if (map.getSource("{self.SOURCE_ID}")) {{
                      map.getSource("{self.SOURCE_ID}").updateImage({{
                          url: imageUrl,
                          coordinates: coords
                      }});
                      // always restore opacity if we are actively injecting an image
                      if (map.getLayer("{self.LAYER_ID}")) {{
                          map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", window._stormRadarOpacity);
                      }}
                  }} else {{
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
                                  "raster-opacity": window._stormRadarOpacity,
                                  "raster-fade-duration": 0
                              }}
                          }}, "road-unpaved");
                      }} catch(e) {{
                          // fallback if beforeId doesn't exist
                          map.addLayer({{
                              id: "{self.LAYER_ID}",
                              type: "raster",
                              source: "{self.SOURCE_ID}",
                              paint: {{
                                  "raster-opacity": window._stormRadarOpacity,
                                  "raster-fade-duration": 0
                              }}
                          }});
                      }}
                  }}
              }}
          }} catch(e) {{ console.error("STORM Inject error:", e); }}
        }})();
        """
        self._map.run_js(js)
