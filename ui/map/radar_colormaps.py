"""The radar products' colormaps (NWS / RadarScope-style), built with
matplotlib on first use. ui/map/radar_overlay.py renders with them; the
lidar views borrow the velocity map."""
from functools import lru_cache


# matplotlib costs ~0.4 s to import, so it loads on the first render, not at startup
def mpl_colors():
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")   # non-interactive backend — no display needed
    import matplotlib.colors as mcolors  # noqa: PLC0415
    return mcolors


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
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_ref",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0), over=_rgba255(115, 10, 1, 255))


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

    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_vel",
        colors,
        N=1024
    )
    return cmap.with_extremes(under=(0, 0, 0, 0), over=(0, 0, 0, 0))


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
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_cc",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


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
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_zdr",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


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
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_sw",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


def _make_nws_phi_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.10, 0.20, 0.70, 0.90)),
        (0.35, (0.20, 0.70, 1.00, 1.00)),
        (0.55, (0.00, 0.85, 0.40, 1.00)),
        (0.75, (1.00, 0.90, 0.00, 1.00)),
        (1.00, (1.00, 0.20, 0.00, 1.00)),
    ]
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_phi",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


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
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_kdp",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


def _make_nws_cfp_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.15, 0.15, 0.18, 0.35)),
        (0.35, (0.28, 0.28, 0.34, 0.60)),
        (0.55, (0.55, 0.55, 0.62, 0.80)),
        (0.75, (0.82, 0.82, 0.86, 0.95)),
        (1.00, (1.00, 1.00, 1.00, 1.00)),
    ]
    cmap = mpl_colors().LinearSegmentedColormap.from_list(
        "nws_cfp",
        [(pos, rgba) for pos, rgba in colors]
    )
    return cmap.with_extremes(under=(0, 0, 0, 0))


# built on first use (see mpl_colors)
@lru_cache(maxsize=1)
def radar_colormaps() -> dict:
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
