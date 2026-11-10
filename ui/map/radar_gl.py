"""
GPU radar rendering prototype (PERFORMANCE_PLAN.md step 4).

Instead of reprojecting and colouring a scan in Python, send the map the raw
8-bit gate codes, an azimuth -> radial table and the 256-entry colour table as
small lossless PNGs.  A WebGL custom layer in map_template.html does the
reprojection and colouring per pixel.

Prototype scope: live Level 3 reflectivity (packed codes, spherical earth, no
reflectivity mask).  Everything else, and any machine in safe map or lite mode,
stays on the PNG path in radar_overlay.
"""
import io
from collections import OrderedDict
import threading

import numpy as np
from PIL import Image

import runtime_flags
from core.radar_scan import RadarScan
from ui.map import radar_overlay as ro

# the azimuth table is ro._AZ_BINS entries laid out as an AZ_TEX_W x AZ_TEX_H texture
AZ_TEX_W = 200
AZ_TEX_H = ro._AZ_BINS // AZ_TEX_W

_az_png_lock = threading.Lock()
_az_png_cache: "OrderedDict[bytes, bytes]" = OrderedDict()
_AZ_PNG_CACHE_MAX = 8


def enabled() -> bool:
    flags = runtime_flags.FLAGS
    return flags.gpu_radar and not flags.safe_map_mode and not flags.lite_mode


def supports(scan: RadarScan, mask_scan: RadarScan | None = None) -> bool:
    """True if the shader can draw this scan exactly like the PNG path."""
    return (
        scan.value_table is not None
        and scan.geodesy == "sphere"
        and scan.colormap == "nws_ref"
        and not ro._mask_applies(scan, mask_scan)
        and scan.raw.shape[0] <= 4096
        and scan.raw.shape[1] <= 4096
    )


def _png(array: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    # compress_level 1: these are re-sent every scan and decoded once, so speed beats size
    Image.fromarray(array, mode).save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _az_png(scan: RadarScan) -> bytes:
    """Radial number per azimuth bin as RGBA: R/G = row low/high byte, B = 255 if valid."""
    key = np.asarray(scan.azimuths_deg, dtype=np.float64).tobytes()
    with _az_png_lock:
        hit = _az_png_cache.get(key)
        if hit is not None:
            _az_png_cache.move_to_end(key)
            return hit

    rows = ro._az_row_table(scan.azimuths_deg)
    valid = rows >= 0
    safe = np.where(valid, rows, 0).astype(np.uint16)
    rgba = np.empty((ro._AZ_BINS, 4), dtype=np.uint8)
    rgba[:, 0] = safe & 0xFF
    rgba[:, 1] = safe >> 8
    rgba[:, 2] = np.where(valid, 255, 0)
    rgba[:, 3] = 255   # opaque, so the browser's alpha handling can't touch R/G/B
    png = _png(rgba.reshape(AZ_TEX_H, AZ_TEX_W, 4), "RGBA")

    with _az_png_lock:
        _az_png_cache[key] = png
        while len(_az_png_cache) > _AZ_PNG_CACHE_MAX:
            _az_png_cache.popitem(last=False)
    return png


def build_payload(scan: RadarScan) -> dict:
    """Everything the WebGL layer needs for one scan.  Thread-safe."""
    return {
        "data_png": _png(np.ascontiguousarray(scan.raw), "L"),
        "az_png": _az_png(scan),
        "lut_png": _png(ro._code_lut(scan).reshape(1, 256, 4), "RGBA"),
        "meta": {
            "radarLat": float(scan.radar_lat),
            "radarLon": float(scan.radar_lon),
            "firstGateM": float(scan.first_gate_m),
            "gateWidthM": float(scan.gate_width_m),
            "farEdgeM": float(ro._far_edge_m(scan)),
            "nAz": int(scan.raw.shape[0]),
            "nGates": int(scan.raw.shape[1]),
            "azTexW": AZ_TEX_W,
            "azTexH": AZ_TEX_H,
            "bounds": ro.footprint_bounds(scan),
        },
    }
