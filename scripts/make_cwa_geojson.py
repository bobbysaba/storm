"""Convert the NWS County Warning Area shapefile to a simplified GeoJSON.

One-off build step; the output is committed under static/ and the map loads it
by URL. Re-run when NWS publishes a new CWA shapefile:

    python scripts/make_cwa_geojson.py cwa_shp/w_16ap26 static/cwa_boundaries.geojson

Simplification uses a 0.001° tolerance (about 100 m, under one pixel at zoom 10)
and coordinates are rounded to 4 decimals (about 11 m).
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from shapely.geometry import Polygon, MultiPolygon, mapping

TOLERANCE_DEG = 0.001
DECIMALS = 4
KEEP_FIELDS = ("WFO", "CWA", "LON", "LAT")


def _signed_area(ring) -> float:
    return sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])
    ) / 2


def read_shp(path: Path) -> list[list[list[tuple[float, float]]] | None]:
    """Rings per record for Polygon (type 5) shapes; None for anything else."""
    data = path.read_bytes()
    pos, out = 100, []
    while pos + 8 <= len(data):
        _rec, words = struct.unpack_from(">ii", data, pos)
        pos += 8
        end = pos + words * 2
        if struct.unpack_from("<i", data, pos)[0] != 5:
            out.append(None)
            pos = end
            continue
        num_parts, num_points = struct.unpack_from("<ii", data, pos + 36)
        starts = struct.unpack_from(f"<{num_parts}i", data, pos + 44)
        pts_off = pos + 44 + num_parts * 4
        raw = struct.unpack_from(f"<{num_points * 2}d", data, pts_off)
        pts = list(zip(raw[0::2], raw[1::2]))
        bounds = list(starts) + [num_points]
        out.append([pts[bounds[i]:bounds[i + 1]] for i in range(num_parts)])
        pos = end
    return out


def read_dbf(path: Path) -> list[dict]:
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 4)[0]
    header, rec_len = struct.unpack_from("<HH", data, 8)
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\x00")[0].decode("ascii").strip()
        fields.append((name, chr(data[pos + 11]), data[pos + 16]))
        pos += 32
    records = []
    for i in range(n):
        p = header + i * rec_len
        if data[p] == 0x2A:  # deleted
            continue
        p += 1
        rec = {}
        for name, ftype, flen in fields:
            raw = data[p:p + flen].decode("ascii", errors="replace").strip()
            if ftype in "NF":
                try:
                    rec[name] = float(raw) if raw else None
                except ValueError:
                    rec[name] = None
            else:
                rec[name] = raw
            p += flen
        records.append(rec)
    return records


def to_geometry(rings):
    """Shapefile rings -> (Multi)Polygon: clockwise rings are outers, the rest holes."""
    polys: list[tuple[list, list]] = []
    for ring in rings:
        if len(ring) < 4:
            continue
        if _signed_area(ring) < 0 or not polys:
            polys.append((ring, []))
        else:
            polys[-1][1].append(ring)
    shapes = []
    for outer, holes in polys:
        simplified = Polygon(outer, holes).simplify(TOLERANCE_DEG, preserve_topology=True)
        if not simplified.is_empty:
            shapes.append(simplified)
    if not shapes:
        return None
    parts = []
    for s in shapes:
        parts.extend(s.geoms if isinstance(s, MultiPolygon) else [s])
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _round_ring(ring):
    """Round a ring and drop repeated points; None if it collapses."""
    out = []
    for x, y in ring:
        pt = [round(x, DECIMALS), round(y, DECIMALS)]
        if not out or pt != out[-1]:
            out.append(pt)
    return out if len(out) >= 4 else None


def _round_polygon(rings):
    rounded = [_round_ring(r) for r in rings]
    if rounded[0] is None:
        return None
    return [r for r in rounded if r is not None]


def _round_geometry(g):
    if g["type"] == "Polygon":
        poly = _round_polygon(g["coordinates"])
        return {"type": "Polygon", "coordinates": poly} if poly else None
    polys = [p for p in map(_round_polygon, g["coordinates"]) if p]
    if not polys:
        return None
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("shp_base", help="shapefile path without extension")
    ap.add_argument("out", help="output .geojson path")
    args = ap.parse_args()

    base = Path(args.shp_base)
    geoms = read_shp(base.with_suffix(".shp"))
    recs = read_dbf(base.with_suffix(".dbf"))
    features = []
    for rings, rec in zip(geoms, recs):
        if not rings:
            continue
        geom = to_geometry(rings)
        if geom is None:
            continue
        g = _round_geometry(mapping(geom))
        if g is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": g,
            "properties": {k: rec[k] for k in KEEP_FIELDS if k in rec},
        })
    Path(args.out).write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
    )
    print(f"{len(features)} features -> {args.out} ({Path(args.out).stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
