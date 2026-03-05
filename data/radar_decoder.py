# data/radar_decoder.py
# Decodes raw NEXRAD Level 3 file bytes into a RadarScan dataclass
# using MetPy's Level3File reader.

import io
import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from metpy.io import Level3File
from metpy.plots.ctables import registry as ctable_registry

from core.radar_scan import RadarScan, PRODUCT_META

log = logging.getLogger(__name__)


def decode_nexrad_l3(site: str, product: str, raw_bytes: bytes) -> Optional[RadarScan]:
    """
    Decode raw NEXRAD Level 3 file bytes into a RadarScan.

    Args:
        site      — 4-letter site ID (e.g. "KTLX")
        product   — product code (e.g. "N0Q", "N0U")
        raw_bytes — raw bytes downloaded from S3

    Returns:
        RadarScan on success, None on failure.
    """
    try:
        f = Level3File(io.BytesIO(raw_bytes))
    except Exception as e:
        log.error("[Decoder] Failed to open Level3File for %s/%s: %s", site, product, e)
        return None

    try:
        # Extract data and coordinates
        # MetPy returns (azimuth, range, data) — we convert to lat/lon grid
        pdata = f.sym_block[0][0]

        raw = np.array(pdata["data"])           # keep uint8 — map_data needs integer indices
        azimuths = np.asarray(pdata["start_az"], dtype=float)
        ranges_m = _extract_ranges_m(pdata, raw.shape[-1], f)

        # Apply scale/offset from MetPy (requires integer input)
        if hasattr(f, "map_data"):
            data = np.array(f.map_data(raw), dtype=float)
        else:
            data = raw.astype(float)

        # Replace missing/masked values with NaN
        if np.ma.is_masked(data):
            data = np.ma.filled(data, np.nan)

        # Convert polar to lat/lon
        lats, lons = _polar_to_latlon(
            azimuths,
            ranges_m,
            f.lat,
            f.lon
        )

        # Scan time
        scan_time = _parse_scan_time(f)

        meta = PRODUCT_META.get(product, {
            "units":    "unknown",
            "vmin":     -32,
            "vmax":     90,
            "colormap": "nws_ref",
        })

        return RadarScan(
            site=site,
            product=product,
            scan_time=scan_time,
            data=data,
            lats=lats,
            lons=lons,
            vmin=meta["vmin"],
            vmax=meta["vmax"],
            units=meta["units"],
            colormap=meta["colormap"],
            az_offset=float(azimuths[0]),
        )

    except Exception as e:
        log.error("[Decoder] Failed to decode %s/%s: %s", site, product, e, exc_info=True)
        return None


def _polar_to_latlon(
    azimuths_deg: np.ndarray,
    ranges_m: np.ndarray,
    center_lat: float,
    center_lon: float
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert polar radar coordinates (azimuth, range) to lat/lon arrays.

    Uses simple flat-earth approximation — accurate enough for radar display
    at ranges up to ~460 km.

    Returns:
        (lats, lons) each shaped (num_azimuths, num_gates)
    """
    R_EARTH_KM = 6371.0

    az_rad = np.deg2rad(azimuths_deg)
    rng_km = ranges_m / 1000.0

    # Meshgrid: rows = azimuths, cols = range gates
    az2d, rng2d = np.meshgrid(az_rad, rng_km, indexing="ij")

    dlat = (rng2d * np.cos(az2d)) / R_EARTH_KM
    dlon = (rng2d * np.sin(az2d)) / (R_EARTH_KM * np.cos(np.deg2rad(center_lat)))

    lats = center_lat + np.rad2deg(dlat)
    lons = center_lon + np.rad2deg(dlon)

    return lats, lons


def _extract_ranges_m(pdata: dict, num_gates: int, f: Level3File) -> np.ndarray:
    """
    Build range array in meters across multiple Level 3 packet variants.
    Some super-res products omit `gate_width` and instead expose other keys.
    """
    gate_width = pdata.get("gate_width")
    if gate_width is None:
        gate_width = pdata.get("gate_interval", pdata.get("gate_size"))

    first_gate = pdata.get("first_gate")
    if first_gate is None:
        first_gate = pdata.get("first_gate_range", pdata.get("start_range", 0.0))

    if gate_width is not None:
        gw = float(gate_width)
        fg = float(first_gate)
        # Heuristic: widths > 20 are usually meters; otherwise kilometers.
        if gw > 20:
            return fg + (gw * np.arange(num_gates, dtype=float))
        return (fg + (gw * np.arange(num_gates, dtype=float))) * 1000.0

    # Fallback: derive from known max_range.
    max_range_km = None
    try:
        max_range_km = float(getattr(f, "max_range"))
    except Exception:
        pass

    if not max_range_km:
        try:
            max_range_km = float(getattr(getattr(f, "prod_info"), "max_range"))
        except Exception:
            pass

    if not max_range_km:
        max_range_km = 460.0

    return np.linspace(0.0, max_range_km * 1000.0, num_gates, endpoint=False, dtype=float)


def _parse_scan_time(f: Level3File) -> datetime:
    """Extract scan time from Level3File, falling back to UTC now."""
    try:
        # MetPy stores time as a datetime on the file object
        if hasattr(f, "prod_desc") and hasattr(f.prod_desc, "vol_scan_date"):
            from metpy.io.nexrad import nexrad_to_datetime
            return nexrad_to_datetime(
                f.prod_desc.vol_scan_date,
                f.prod_desc.vol_scan_time
            ).replace(tzinfo=timezone.utc)
    except Exception:
        pass

    try:
        if hasattr(f, "metadata") and "vol_time" in f.metadata:
            return f.metadata["vol_time"].replace(tzinfo=timezone.utc)
    except Exception:
        pass

    return datetime.now(timezone.utc)
