"""Reading the SPC shapefile zips the archive hazard fetcher downloads
(archive/fetchers/hazard_archive_fetcher.py): IEM's mesoscale discussion
zips, and SPC's archived outlook zips."""
import io
import logging
import struct
import zipfile
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# the outlook zips' categorical DN codes
_SPC_CAT_DN_LABELS = {2: "MRGL", 3: "SLGT", 4: "ENH", 5: "MDT", 6: "HIGH"}


def parse_mcd_zip(zip_bytes: bytes, filter_time: datetime) -> list:
    """
    Parse the IEM GIS shapefile zip for MCDs.
    Returns a list of GeoJSON Feature dicts filtered to those valid at filter_time.

    The zip contains: .shp (Polygon type 5), .dbf (with ISSUE/EXPIRE/NUM fields).
    DBF field ISSUE and EXPIRE are 12-char strings in YYYYMMDDHHmm format.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        log.warning("MCD shapefile: bad zip: %s", exc)
        return []

    # find .shp and .dbf members (case-insensitive).
    names = zf.namelist()
    shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
    dbf_name = next((n for n in names if n.lower().endswith(".dbf")), None)
    if not shp_name or not dbf_name:
        log.warning("MCD shapefile: missing .shp or .dbf in zip (files: %s)", names)
        return []

    shp_data = zf.read(shp_name)
    dbf_data = zf.read(dbf_name)

    geometries = _parse_shp(shp_data)
    records    = _parse_dbf(dbf_data)

    if len(geometries) != len(records):
        log.warning(
            "MCD shapefile: geometry count %d != record count %d",
            len(geometries), len(records)
        )
        # still proceed with min(len) pairs.

    features = []
    for geom, rec in zip(geometries, records):
        if geom is None:
            continue
        issue_str  = (rec.get("ISSUE")  or "").strip()
        expire_str = (rec.get("EXPIRE") or "").strip()
        num_raw    = rec.get("NUM")
        try:
            num = int(float(num_raw)) if num_raw is not None else 0
        except (TypeError, ValueError):
            num = 0
        if not num:
            continue

        # parse ISSUE/EXPIRE (YYYYMMDDHHmm, 12 chars).
        issue  = _parse_yyyymmddHHMM(issue_str)
        expire = _parse_yyyymmddHHMM(expire_str)
        if issue is None or expire is None:
            continue
        if not (issue <= filter_time <= expire):
            continue

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": dict(rec, num=num),
        })

    return features


def _parse_shp(data: bytes, transform=None) -> list:
    """
    Minimal parser for ESRI Shapefile (.shp).
    Supports Polygon (type 5) only.  Returns a list of GeoJSON geometry dicts
    (or None for null/unsupported records).

    `transform`, if given, is a callable (x, y) -> (x2, y2) applied to every
    raw point as it's read -- SPC's shapefiles store coordinates in a
    projected CRS (Lambert Conformal Conic; see
    parse_outlook_zip), not GeoJSON's required WGS84 lon/lat, so
    the real caller always passes a proper reprojection. None (the
    default) is an identity no-op, kept for callers/tests that already
    hand in lon/lat-like values directly.
    """
    if len(data) < 100:
        return []

    pos = 100  # skip 100-byte file header
    geometries = []

    while pos < len(data):
        if pos + 12 > len(data):
            break
        # record header: record number (big-endian int32), content length (big-endian int32, in 16-bit words).
        _rec_num, content_words = struct.unpack_from(">ii", data, pos)
        pos += 8
        content_bytes = content_words * 2
        if content_bytes < 4 or pos + content_bytes > len(data):
            break
        shape_type = struct.unpack_from("<i", data, pos)[0]
        if shape_type == 0:
            # null shape.
            geometries.append(None)
            pos += content_bytes
            continue
        if shape_type != 5:
            # non-polygon; skip.
            geometries.append(None)
            pos += content_bytes
            continue

        # polygon: bounding box (4 doubles), num_parts (int32), num_points (int32),
        offset = pos + 4  # skip shape_type field
        if offset + 32 + 8 > len(data):
            geometries.append(None)
            pos += content_bytes
            continue
        offset += 32  # skip bounding box
        num_parts, num_points = struct.unpack_from("<ii", data, offset)
        offset += 8
        if num_parts <= 0 or num_points <= 0:
            geometries.append(None)
            pos += content_bytes
            continue
        part_starts = list(struct.unpack_from(f"<{num_parts}i", data, offset))
        offset += num_parts * 4
        pts_raw = struct.unpack_from(f"<{num_points * 2}d", data, offset)
        if transform is None:
            points = [(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]
        else:
            points = [transform(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]

        # split points into rings using part_starts.
        rings = []
        for idx_r, start in enumerate(part_starts):
            end = part_starts[idx_r + 1] if idx_r + 1 < num_parts else num_points
            ring = [list(pt) for pt in points[start:end]]
            rings.append(ring)

        geometries.append({"type": "Polygon", "coordinates": rings})
        pos += content_bytes

    return geometries


def _parse_dbf(data: bytes) -> list:
    """
    Minimal parser for dBASE III+ (.dbf).
    Returns a list of dicts, one per record, with string or numeric values.
    """
    if len(data) < 32:
        return []

    # header: version(1), date(3), num_records(4LE), header_bytes(2LE), record_bytes(2LE).
    num_records  = struct.unpack_from("<I", data, 4)[0]
    header_bytes = struct.unpack_from("<H", data, 8)[0]
    record_bytes = struct.unpack_from("<H", data, 10)[0]

    # field descriptors start at byte 32, each 32 bytes, terminated by 0x0D.
    fields = []
    pos = 32
    while pos < header_bytes - 1 and data[pos] != 0x0D:
        if pos + 32 > len(data):
            break
        raw_name = data[pos:pos + 11]
        name  = raw_name.split(b"\x00")[0].decode("ascii", errors="replace").strip()
        ftype = chr(data[pos + 11])
        flen  = data[pos + 16]
        fields.append((name, ftype, flen))
        pos += 32

    records = []
    rec_pos = header_bytes
    for _ in range(num_records):
        if rec_pos + record_bytes > len(data):
            break
        deletion_flag = data[rec_pos]
        if deletion_flag == 0x2A:  # '*' = deleted
            rec_pos += record_bytes
            continue
        field_pos = rec_pos + 1  # skip deletion flag
        rec = {}
        for name, ftype, flen in fields:
            raw = data[field_pos:field_pos + flen].decode("ascii", errors="replace").strip()
            if ftype == "N":
                try:
                    rec[name] = float(raw) if raw else None
                except ValueError:
                    rec[name] = None
            else:
                rec[name] = raw
            field_pos += flen
        records.append(rec)
        rec_pos += record_bytes

    return records


def _shapefile_lonlat_transform(prj_wkt: str):
    """Build an (x, y) -> (lon, lat) callable from a shapefile's own .prj
    (ESRI WKT) contents. Confirmed live (2026-09) against a real archive
    zip: SPC's outlook shapefiles are Lambert Conformal Conic (standard
    parallels 33N/45N, central meridian 0), not GeoJSON's required WGS84
    lon/lat -- every product's .prj in a given zip carries the identical
    definition. Raw .shp coordinates used directly as lon/lat (this
    fallback's original behavior) put every polygon at a wildly wrong
    location -- e.g. one real Oklahoma categorical-outlook ring's raw
    coordinates inverse-projected as plain Web Mercator landed in
    northern Canada, nowhere near the visible map extent.
    """
    from pyproj import CRS, Transformer
    crs = CRS.from_wkt(prj_wkt)
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return lambda x, y: transformer.transform(x, y)


# Degrees, applied post-reprojection. SPC's pre-2020 archive shapefiles are
# raw, unsmoothed marching-squares contours off their underlying probability
# grid -- confirmed live: a real outlook ring had thousands of vertices in an
# alternating-diagonal staircase pattern (e.g. consecutive vertex deltas of
# (0.050, -0.021) then (0.026, 0.040) repeating), nothing like the smoother
# curves SPC's own 2020+ .lyr.geojson archive already publishes pre-smoothed.
# ~0.05 deg (~5 km over CONUS) removes that staircase noise without visibly
# eating into the outlook's real shape at the zoom levels this renders at.
_OUTLOOK_SIMPLIFY_TOLERANCE_DEG = 0.05


def _simplify_outlook_geometry(geom: dict, tolerance_deg: float) -> dict:
    """Douglas-Peucker-simplify a reprojected Polygon's ring(s) to smooth
    out grid-contour staircasing. Falls back to the original geometry
    whenever shapely can't produce a valid, non-empty result (e.g. a
    sliver ring simplifying away entirely) -- a jagged polygon is still
    correct; a missing one wouldn't be."""
    if geom.get("type") != "Polygon":
        return geom
    try:
        from shapely.geometry import mapping, shape
        simplified = shape(geom).simplify(tolerance_deg, preserve_topology=True)
        if simplified.is_empty or not simplified.is_valid or simplified.geom_type != "Polygon":
            return geom
        return mapping(simplified)
    except Exception as exc:
        log.warning("Outlook shapefile zip: geometry simplification failed, using raw ring: %s", exc)
        return geom


def parse_outlook_zip(zip_bytes: bytes) -> dict[str, list[dict]]:
    """Parse a pre-2020 outlook zip into {suffix: [GeoJSON Feature, ...]},
    keyed by the same suffix strings _archive_product_suffixes /
    _archive_sig_suffixes use (cat, wind, hail, torn, sigwind, sighail,
    sigtorn) -- one member pair per suffix, named
    day{N}otlk_..._{suffix}.shp/.dbf. Each record's only real field is a
    numeric DN: the categorical outlook's risk-level code (2-6, mapped via
    _SPC_CAT_DN_LABELS) or the probabilistic layers' percent-contour value,
    which _spc_prob_label already reads directly off props["DN"].
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        log.warning("Outlook shapefile zip: bad zip: %s", exc)
        return {}

    names = zf.namelist()
    # Every product's .prj in a real zip carries the identical projection
    # (confirmed live) -- read whichever one is present once, rather than
    # re-parsing it per suffix. Without a usable transform, raw .shp
    # coordinates (a projected CRS, not lon/lat) would misplace every
    # polygon, so this is treated the same as an unreadable zip rather
    # than silently emitting geometry at the wrong location.
    prj_name = next((n for n in names if n.lower().endswith(".prj")), None)
    if prj_name is None:
        log.warning("Outlook shapefile zip: no .prj found; refusing to guess a coordinate system")
        return {}
    try:
        transform = _shapefile_lonlat_transform(zf.read(prj_name).decode("ascii", errors="replace"))
    except Exception as exc:
        log.warning("Outlook shapefile zip: could not parse %s: %s", prj_name, exc)
        return {}

    by_suffix: dict[str, list[dict]] = {}
    for suffix in ("cat", "wind", "hail", "torn", "sigwind", "sighail", "sigtorn"):
        shp_name = next((n for n in names if n.lower().endswith(f"_{suffix}.shp")), None)
        dbf_name = next((n for n in names if n.lower().endswith(f"_{suffix}.dbf")), None)
        if not shp_name or not dbf_name:
            continue
        geometries = _parse_shp(zf.read(shp_name), transform=transform)
        records = _parse_dbf(zf.read(dbf_name))

        features = []
        for geom, rec in zip(geometries, records):
            if geom is None:
                continue
            geom = _simplify_outlook_geometry(geom, _OUTLOOK_SIMPLIFY_TOLERANCE_DEG)
            dn_raw = rec.get("DN")
            try:
                dn = int(dn_raw) if dn_raw is not None else None
            except (TypeError, ValueError):
                dn = None
            if dn is None:
                continue
            if suffix.startswith("sig") and dn <= 0:
                # DN=0 on a sig* layer is SPC's "no significant risk"
                # placeholder -- the shapefile format needs at least one
                # record even when there's nothing to draw, so it ships a
                # degenerate polygon instead of an empty file. Confirmed
                # against a real archive day (2019-06-08): sigwind/sigtorn
                # both carried a tiny throwaway DN=0 polygon that day, while
                # sighail's real DN=10 significant-hail contour came through
                # as expected. Rendering the DN=0 one would draw a false
                # "significant" overlay where none exists.
                continue
            props = dict(rec, DN=dn)
            if suffix == "cat":
                label = _SPC_CAT_DN_LABELS.get(dn)
                if label is None:
                    continue
                props["LABEL"] = label
            features.append({"type": "Feature", "geometry": geom, "properties": props})
        by_suffix[suffix] = features

    return by_suffix


def _parse_yyyymmddHHMM(s: str) -> Optional[datetime]:
    """Parse a 12-char YYYYMMDDHHmm string into a UTC-aware datetime."""
    if not s or len(s) < 12:
        return None
    try:
        return datetime(
            int(s[0:4]), int(s[4:6]),  int(s[6:8]),
            int(s[8:10]), int(s[10:12]),
            tzinfo=timezone.utc,
        )
    except (ValueError, OverflowError):
        return None
