
import io
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import numpy as np

from core.radar_scan import RadarScan, PRODUCT_META

if TYPE_CHECKING:
    from metpy.io import Level3File

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
        # metpy is heavy; import on first decode, normally on a fetch thread
        from metpy.io import Level3File
        f = Level3File(io.BytesIO(raw_bytes))
    except Exception as e:
        log.error("[Decoder] Failed to open Level3File for %s/%s: %s", site, product, e)
        return None

    try:
        # extract data and coordinates
        pdata = f.sym_block[0][0]

        raw = np.array(pdata["data"])           # keep uint8 — map_data needs integer indices
        # nexrad `start_az` is the leading edge of each radial; the sample
        start_az = np.asarray(pdata["start_az"], dtype=float)
        beam_width = _estimate_beam_width(start_az)
        azimuths = start_az + 0.5 * beam_width
        ranges_m = _extract_ranges_m(pdata, raw.shape[-1], f)

        meta = PRODUCT_META.get(product, {
            "units":    "unknown",
            "vmin":     -32,
            "vmax":     90,
            "colormap": "nws_ref",
        })

        # apply scale/offset from MetPy (requires integer input)
        if hasattr(f, "map_data"):
            data = np.array(f.map_data(raw), dtype=np.float32)
            # MetPy's DigitalVelMapper returns m/s; convert to knots to match
            # the kt-based vmin/vmax and colormap stops (mirrors L2 archive path).
            if getattr(f.map_data, "units", None) == "m/s" and meta.get("units") == "kt":
                data *= 1.94384
        else:
            data = raw.astype(np.float32)

        # replace missing/masked values with NaN
        if np.ma.is_masked(data):
            data = np.ma.filled(data, np.nan)

        # Level 3 is 8-bit on the wire and MetPy's mappers are pure per-code
        # lookups, so keep the packed codes plus a 256-entry value table
        # instead of a float per gate (lossless, 1 byte per gate).
        if raw.dtype == np.uint8:
            value_table = np.full(256, np.nan, dtype=np.float32)
            value_table[raw] = data
            stored = raw
        else:
            value_table = None
            stored = data.astype(np.float16)

        # scan time
        scan_time = _parse_scan_time(f)

        return RadarScan(
            site=site,
            product=product,
            scan_time=scan_time,
            raw=stored,
            vmin=meta["vmin"],
            vmax=meta["vmax"],
            units=meta["units"],
            colormap=meta["colormap"],
            radar_lat=f.lat,
            radar_lon=f.lon,
            azimuths_deg=azimuths,
            first_gate_m=float(ranges_m[0]),
            gate_width_m=float(ranges_m[1] - ranges_m[0]) if ranges_m.size > 1 else float(ranges_m[0]) * 2.0,
            value_table=value_table,
        )

    except Exception as e:
        log.error("[Decoder] Failed to decode %s/%s: %s", site, product, e, exc_info=True)
        return None


def _estimate_beam_width(start_az_deg: np.ndarray) -> float:
    """Median spacing between consecutive radials, in degrees.

    Handles the 360°→0° wrap and falls back to 1.0° (legacy resolution) if
    the array is degenerate.
    """
    if start_az_deg.size < 2:
        return 1.0
    diffs = np.diff(start_az_deg)
    diffs = (diffs + 180.0) % 360.0 - 180.0
    bw = float(np.median(np.abs(diffs)))
    if not np.isfinite(bw) or bw <= 0:
        return 1.0
    return bw


def _extract_ranges_m(pdata: dict, num_gates: int, f: "Level3File") -> np.ndarray:
    """
    Build range array in meters across multiple Level 3 packet variants,
    returning the **center** of each gate (so polar→latlon places samples
    at gate centers, not at the near edge).

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
        # heuristic: widths > 20 are usually meters; otherwise kilometers.
        if gw > 20:
            return fg + gw * (np.arange(num_gates, dtype=float) + 0.5)
        return (fg + gw * (np.arange(num_gates, dtype=float) + 0.5)) * 1000.0

    # fallback: derive from known max_range.
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

    # gate-center spacing: place samples at half-gate intervals from 0.
    gw_m = (max_range_km * 1000.0) / num_gates
    return (np.arange(num_gates, dtype=float) + 0.5) * gw_m


def _parse_scan_time(f: "Level3File") -> datetime:
    """Extract scan time from Level3File, falling back to UTC now."""
    try:
        # metPy stores time as a datetime on the file object
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
