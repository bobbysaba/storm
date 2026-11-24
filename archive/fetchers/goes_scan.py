"""Reading a GOES ABI scan (netCDF, whole or by byte ranges) and rendering
it as a Web Mercator PNG, for the archive satellite fetcher
(archive/fetchers/satellite_archive_fetcher.py)."""
import io
import logging
import math
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import numpy as np
import requests

from core import package_sources, provenance
from core.goes_ir import ir_rgb

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
_OUTPUT_WIDTH = {"conus": 1500, "meso1": 1100, "meso2": 1100}

_MERC_R = 6378137.0
# (mode, grid geometry, output size) -> (flat source index per output pixel, valid mask)
_INDEX_CACHE: "OrderedDict[tuple, tuple[np.ndarray, np.ndarray]]" = OrderedDict()
_INDEX_CACHE_SIZE = 4
_INDEX_LOCK = threading.Lock()


@dataclass(frozen=True)
class _GoesGrid:
    lon0: float        # radians
    a: float           # semi-major axis, m
    b: float           # semi-minor axis, m
    H: float           # distance from earth centre to satellite, m
    x0: float          # scan angle of column 0, radians
    dx: float
    nx: int
    y0: float          # scan angle of row 0, radians
    dy: float
    ny: int


def _attr(obj, name, default=None):
    val = obj.attrs.get(name, default)
    if isinstance(val, np.ndarray):
        val = val.reshape(-1)[0]
    if isinstance(val, bytes):
        val = val.decode()
    return val


@dataclass
class _Scan:
    """The part of a GOES CMI file a frame needs: raw counts (scale / offset
    applied only to the pixels drawn) over the window ``grid`` describes."""
    counts: np.ndarray
    fill: Optional[int]
    scale: float
    offset: float
    grid: "_GoesGrid"     # of the window, not the whole file
    bbox: list[float]
    size: tuple[int, int]
    k: int


def _read_grid(f) -> "_GoesGrid":
    proj = f["goes_imager_projection"]
    a = float(_attr(proj, "semi_major_axis"))
    xv, yv = f["x"], f["y"]
    return _GoesGrid(
        lon0=math.radians(float(_attr(proj, "longitude_of_projection_origin"))),
        a=a,
        b=float(_attr(proj, "semi_minor_axis")),
        H=float(_attr(proj, "perspective_point_height")) + a,
        x0=float(_attr(xv, "add_offset")) + float(xv[0]) * float(_attr(xv, "scale_factor")),
        dx=float(_attr(xv, "scale_factor")),
        nx=int(xv.shape[0]),
        y0=float(_attr(yv, "add_offset")) + float(yv[0]) * float(_attr(yv, "scale_factor")),
        dy=float(_attr(yv, "scale_factor")),
        ny=int(yv.shape[0]),
    )


def _scan_window(g: "_GoesGrid", bbox: list[float], k: int) -> tuple[int, int, int, int]:
    """(row0, row1, col0, col1) of the source pixels covering ``bbox``, padded
    by one box-average block and starting on a block boundary of the whole
    grid (so frames average exactly as a full read would). Mercator edges are
    curves in scan space, so the whole outline is sampled, not only its corners."""
    west, south, east, north = bbox
    t = np.linspace(0.0, 1.0, 64)
    lon = np.concatenate([west + t * (east - west), np.full(64, east),
                          east - t * (east - west), np.full(64, west)])
    lat = np.concatenate([np.full(64, south), south + t * (north - south),
                          np.full(64, north), north - t * (north - south)])
    x, y, visible = _lonlat_to_scan(g, np.radians(lon), np.radians(lat))
    if not visible.any():
        return 0, g.ny, 0, g.nx
    cols = (x[visible] - g.x0) / g.dx
    rows = (y[visible] - g.y0) / g.dy
    c0 = max(0, (int(math.floor(cols.min())) // k - 1) * k)
    c1 = min(g.nx, int(math.ceil(cols.max())) + 1 + k)
    r0 = max(0, (int(math.floor(rows.min())) // k - 1) * k)
    r1 = min(g.ny, int(math.ceil(rows.max())) + 1 + k)
    if c0 >= c1 or r0 >= r1:
        return 0, g.ny, 0, g.nx
    return r0, r1, c0, c1


def _sub_grid(g: "_GoesGrid", r0: int, r1: int, c0: int, c1: int) -> "_GoesGrid":
    return _GoesGrid(lon0=g.lon0, a=g.a, b=g.b, H=g.H,
                     x0=g.x0 + c0 * g.dx, dx=g.dx, nx=c1 - c0,
                     y0=g.y0 + r0 * g.dy, dy=g.dy, ny=r1 - r0)


def _chunk_spans(dset, r0: int, r1: int, c0: int, c1: int) -> list[tuple[int, int]]:
    """(byte offset, size) of the dataset's stored chunks that overlap the window."""
    chunks = dset.chunks
    if chunks is None:
        return []
    ch, cw = chunks[:2]
    spans: list[tuple[int, int]] = []

    def visit(info):
        row, col = info.chunk_offset[:2]
        if row < r1 and row + ch > r0 and col < c1 and col + cw > c0:
            spans.append((info.byte_offset, info.size))

    if hasattr(dset.id, "chunk_iter"):
        dset.id.chunk_iter(visit)
    else:
        for i in range(dset.id.get_num_chunks()):
            visit(dset.id.get_chunk_info(i))
    return spans


def _read_goes(source, mode: str, fixed_bbox: Optional[list[float]] = None) -> _Scan:
    """Read the CMI counts a frame needs from ``source`` (bytes, or a
    _RangeFile, whose chunks for the window are prefetched first)."""
    import h5py

    fileobj = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with h5py.File(fileobj, "r") as f:
        g = _read_grid(f)
        bbox = list(fixed_bbox) if fixed_bbox else _grid_bbox(g)
        size = _output_size(mode, bbox)
        k = _block_factor(g, bbox, size[0])
        if fixed_bbox:
            r0, r1, c0, c1 = _scan_window(g, bbox, k)
        else:
            r0, r1, c0, c1 = 0, g.ny, 0, g.nx
        cmi = f["CMI"]
        if isinstance(fileobj, _RangeFile):
            fileobj.prefetch(_chunk_spans(cmi, r0, r1, c0, c1))
        counts = cmi[r0:r1, c0:c1]
        fill = _attr(cmi, "_FillValue")
        if str(_attr(cmi, "_Unsigned", "false")).lower() == "true":
            counts = counts.view(np.uint16)
            if fill is not None:
                fill = int(np.int16(fill).view(np.uint16))
        return _Scan(
            counts=counts,
            fill=None if fill is None else int(fill),
            scale=float(_attr(cmi, "scale_factor", 1.0)),
            offset=float(_attr(cmi, "add_offset", 0.0)),
            grid=_sub_grid(g, r0, r1, c0, c1),
            bbox=bbox,
            size=size,
            k=k,
        )


# range reads: block size, gap between wanted chunks still fetched in one
# request, and parallel requests
_RANGE_BLOCK = 64 * 1024
_RANGE_MERGE_GAP = 256 * 1024
_RANGE_WORKERS = 4


class _WholeFile(Exception):
    """The server ignored the Range header and sent the whole file."""

    def __init__(self, content: bytes):
        super().__init__("range request answered with the whole file")
        self.content = content


class _RangeFile(io.RawIOBase):
    """A read-only remote file for h5py, fetched with HTTP range requests in
    64 KB blocks that are kept once read. HDF5's metadata then costs a few
    small requests, and ``prefetch`` pulls a window's data chunks in a
    handful of parallel ones, instead of downloading the whole file."""

    def __init__(self, url: str, timeout: float = REQUEST_TIMEOUT):
        super().__init__()
        self.url = url
        self._timeout = timeout
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_maxsize=_RANGE_WORKERS)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._blocks: dict[int, bytes] = {}
        self._lock = threading.Lock()
        self._pos = 0
        self.size: Optional[int] = None
        self.requests = 0
        self.fetched = 0
        self._fetch_blocks(0, 4)      # the superblock and, usually, most metadata

    def close(self) -> None:
        # also runs at garbage collection when __init__ failed before the session existed
        session = getattr(self, "_session", None)
        if session is not None:
            session.close()
        super().close()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = (self.size or 0) + offset
        return self._pos

    def readinto(self, buffer) -> int:
        size = self.size or 0
        end = min(self._pos + len(buffer), size)
        if end <= self._pos:
            return 0
        first, last = self._pos // _RANGE_BLOCK, (end - 1) // _RANGE_BLOCK
        missing = [b for b in range(first, last + 1) if b not in self._blocks]
        if missing:
            self._fetch_blocks(missing[0], missing[-1] - missing[0] + 1)
        view = memoryview(buffer)
        out = 0
        pos = self._pos
        while pos < end:
            block = self._blocks[pos // _RANGE_BLOCK]
            at = pos % _RANGE_BLOCK
            n = min(len(block) - at, end - pos)
            view[out:out + n] = block[at:at + n]
            out += n
            pos += n
        self._pos = end
        return out

    def prefetch(self, spans: list[tuple[int, int]]) -> None:
        """Fetch the blocks under these (offset, size) spans, nearby spans
        merged into one request, the requests in parallel."""
        wanted = sorted(spans)
        runs: list[list[int]] = []
        for offset, length in wanted:
            if runs and offset <= runs[-1][1] + _RANGE_MERGE_GAP:
                runs[-1][1] = max(runs[-1][1], offset + length)
            else:
                runs.append([offset, offset + length])
        jobs = []
        for start, stop in runs:
            first, last = start // _RANGE_BLOCK, (stop - 1) // _RANGE_BLOCK
            todo = [b for b in range(first, last + 1) if b not in self._blocks]
            if todo:
                jobs.append((todo[0], todo[-1] - todo[0] + 1))
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=_RANGE_WORKERS, thread_name_prefix="sat-range") as pool:
            for result in [pool.submit(self._fetch_blocks, first, count) for first, count in jobs]:
                result.result()

    def _fetch_blocks(self, first: int, count: int) -> None:
        start = first * _RANGE_BLOCK
        stop = (first + count) * _RANGE_BLOCK
        if self.size is not None:
            stop = min(stop, self.size)
        resp = self._session.get(self.url, headers={"Range": f"bytes={start}-{stop - 1}"},
                                 timeout=self._timeout)
        if resp.status_code == 200:
            raise _WholeFile(resp.content)
        resp.raise_for_status()
        if resp.status_code != 206:
            raise RuntimeError(f"range request answered {resp.status_code}")
        total = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1]
        data = resp.content
        with self._lock:
            self.requests += 1
            self.fetched += len(data)
            if self.size is None and total.isdigit():
                self.size = int(total)
            for i in range(0, len(data), _RANGE_BLOCK):
                self._blocks[first + i // _RANGE_BLOCK] = data[i:i + _RANGE_BLOCK]


def load_scan(url: str, mode: str, fixed_bbox: Optional[list[float]], ranged: bool = False) -> _Scan:
    """A frame's counts from a case package's copy, else by range requests
    (``ranged``: only the window's chunks, for the large visible CONUS files),
    else by downloading the whole file."""
    packaged = package_sources.get(url)
    if packaged is not None:
        return _read_goes(packaged, mode, fixed_bbox)
    if ranged:
        try:
            remote = _RangeFile(url)
            try:
                scan = _read_goes(remote, mode, fixed_bbox)
            finally:
                remote.close()
            # no whole-file hash: an export downloads the file in full
            provenance.record("satellite", url, size=remote.size, source="network (ranges)")
            log.debug("ArchiveSatelliteFetcher: %s by %d range requests, %.1f of %.1f MB",
                      url.rsplit("/", 1)[-1], remote.requests, remote.fetched / 1e6, (remote.size or 0) / 1e6)
            return scan
        except _WholeFile as whole:
            provenance.record("satellite", url, whole.content)
            return _read_goes(whole.content, mode, fixed_bbox)
        except Exception as exc:  # noqa: BLE001 -- the plain download below still works
            log.warning("ArchiveSatelliteFetcher: range read of %s failed (%s); downloading it whole",
                        url.rsplit("/", 1)[-1], exc)
    resp = package_sources.requests_get("satellite", url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _read_goes(resp.content, mode, fixed_bbox)


def _lonlat_to_scan(g: _GoesGrid, lon: np.ndarray, lat: np.ndarray):
    """GOES-R PUG forward projection: geodetic lon/lat (radians) -> scan angles.
    Returns (x, y, visible)."""
    e2 = (g.a * g.a - g.b * g.b) / (g.a * g.a)
    lat_c = np.arctan((g.b * g.b) / (g.a * g.a) * np.tan(lat))
    cos_c = np.cos(lat_c)
    rc = g.b / np.sqrt(1.0 - e2 * cos_c * cos_c)
    dlon = lon - g.lon0
    sx = g.H - rc * cos_c * np.cos(dlon)
    sy = -rc * cos_c * np.sin(dlon)
    sz = rc * np.sin(lat_c)
    x = np.arcsin(-sy / np.sqrt(sx * sx + sy * sy + sz * sz))
    y = np.arctan(sz / sx)
    visible = g.H * (g.H - sx) >= sy * sy + (g.a * g.a) / (g.b * g.b) * sz * sz
    return x, y, visible


def _scan_to_lonlat(g: _GoesGrid, x: float, y: float) -> Optional[tuple[float, float]]:
    """Inverse projection for one scan-angle pair; None when it misses the earth."""
    ab2 = (g.a * g.a) / (g.b * g.b)
    qa = math.sin(x) ** 2 + math.cos(x) ** 2 * (math.cos(y) ** 2 + ab2 * math.sin(y) ** 2)
    qb = -2.0 * g.H * math.cos(x) * math.cos(y)
    qc = g.H * g.H - g.a * g.a
    disc = qb * qb - 4.0 * qa * qc
    if disc < 0:
        return None
    rs = (-qb - math.sqrt(disc)) / (2.0 * qa)
    sx = rs * math.cos(x) * math.cos(y)
    sy = -rs * math.sin(x)
    sz = rs * math.cos(x) * math.sin(y)
    lat = math.atan(ab2 * sz / math.sqrt((g.H - sx) ** 2 + sy * sy))
    lon = g.lon0 - math.atan(sy / (g.H - sx))
    return math.degrees(lon), math.degrees(lat)


def _grid_bbox(g: _GoesGrid) -> list[float]:
    """Lon/lat bbox of the grid's corner pixels (meso files carry no geospatial_* attrs)."""
    pts = [
        _scan_to_lonlat(g, g.x0 + i * g.dx, g.y0 + j * g.dy)
        for i in (0, g.nx - 1) for j in (0, g.ny - 1)
    ]
    pts = [p for p in pts if p is not None]
    if not pts:
        raise RuntimeError("GOES file is missing geospatial bounds")
    lons, lats = zip(*pts)
    return [min(lons), min(lats), max(lons), max(lats)]


def _merc_y(lat_deg: float) -> float:
    return _MERC_R * math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def _output_size(mode: str, bbox: list[float]) -> tuple[int, int]:
    west, south, east, north = bbox
    width = _OUTPUT_WIDTH.get(mode, 1100)
    x_span = math.radians(max(east - west, 1e-6)) * _MERC_R
    y_span = max(_merc_y(north) - _merc_y(south), 1e-6)
    return width, max(1, int(round(width * y_span / x_span)))


def _block_factor(g: _GoesGrid, bbox: list[float], width: int) -> int:
    """Source pixels per output pixel (rounded down), measured across the bbox centre.
    The source is box-averaged by this factor so downsampling doesn't alias."""
    west, south, east, north = bbox
    lat = math.radians((south + north) / 2)
    xs, _, _ = _lonlat_to_scan(
        g, np.radians(np.array([west, east])), np.array([lat, lat])
    )
    src_cols = abs(float(xs[1] - xs[0])) / g.dx
    return max(1, int(src_cols / width))


def _resample_index(g: _GoesGrid, k: int, bbox: list[float], size: tuple[int, int], key: tuple):
    with _INDEX_LOCK:
        hit = _INDEX_CACHE.get(key)
        if hit is not None:
            _INDEX_CACHE.move_to_end(key)
            return hit

    west, south, east, north = bbox
    width, height = size
    # web mercator pixel centres: equal steps in x (lon) and in mercator y
    lon = np.radians(west + (np.arange(width) + 0.5) * (east - west) / width)
    my_top, my_bot = _merc_y(north), _merc_y(south)
    my = my_top - (np.arange(height) + 0.5) * (my_top - my_bot) / height
    lat = 2.0 * np.arctan(np.exp(my / _MERC_R)) - math.pi / 2
    lon2, lat2 = np.meshgrid(lon, lat)
    x, y, visible = _lonlat_to_scan(g, lon2, lat2)

    nxk, nyk = g.nx // k, g.ny // k
    # x0/y0 are pixel centres; block i spans source pixels [i*k, (i+1)*k)
    col = np.floor((x - g.x0 + 0.5 * g.dx) / (g.dx * k)).astype(np.int64)
    row = np.floor((y - g.y0 + 0.5 * g.dy) / (g.dy * k)).astype(np.int64)
    valid = visible & (col >= 0) & (col < nxk) & (row >= 0) & (row < nyk)
    flat = np.where(valid, row * nxk + col, 0).astype(np.int32).ravel()
    result = (flat, valid.ravel())

    with _INDEX_LOCK:
        _INDEX_CACHE[key] = result
        while len(_INDEX_CACHE) > _INDEX_CACHE_SIZE:
            _INDEX_CACHE.popitem(last=False)
    return result


def _box_average_counts(counts: np.ndarray, fill: Optional[int], k: int) -> np.ndarray:
    """k x k block mean of raw counts, skipping fill (NaN where a block is all
    fill); trailing rows/cols that don't fill a block are dropped. Worked in
    strips, so no full-size float copy of the counts is ever made."""
    nyk, nxk = counts.shape[0] // k, counts.shape[1] // k
    out = np.empty((nyk, nxk), dtype=np.float32)
    strip = max(1, 2048 // k)          # block rows per pass
    for i in range(0, nyk, strip):
        j = min(nyk, i + strip)
        blocks = counts[i * k:j * k, :nxk * k].reshape(j - i, k, nxk, k)
        if fill is None:
            out[i:j] = blocks.mean(axis=(1, 3), dtype=np.float64)
            continue
        valid = blocks != fill
        total = np.where(valid, blocks, 0).sum(axis=(1, 3), dtype=np.float64)
        count = valid.sum(axis=(1, 3))
        with np.errstate(invalid="ignore", divide="ignore"):
            out[i:j] = np.where(count > 0, total / count, np.nan)
    return out


def _sample_scan(scan: _Scan) -> tuple[np.ndarray, np.ndarray]:
    """(values in physical units, valid) for every output pixel, nearest
    block; scale / offset are applied to these pixels only."""
    g, k = scan.grid, scan.k
    key = (g, k, tuple(round(v, 4) for v in scan.bbox), scan.size)
    flat, valid = _resample_index(g, k, scan.bbox, scan.size, key)
    if k == 1:
        picked = scan.counts.reshape(-1)[flat]
        valid = valid & (picked != scan.fill) if scan.fill is not None else valid.copy()
        values = picked.astype(np.float32)
    else:
        values = _box_average_counts(scan.counts, scan.fill, k).reshape(-1)[flat]
        valid = valid & np.isfinite(values)
    values = values * np.float32(scan.scale) + np.float32(scan.offset)
    return values, valid


def _encode_grey_alpha(grey: np.ndarray, alpha: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.dstack([grey, alpha]), "LA").save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def _encode_rgba(rgb: np.ndarray, alpha: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.dstack([rgb, alpha[..., None]]), "RGBA").save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def render_scan(scan: _Scan, band: str = "vis") -> bytes:
    """Encode a scan resampled onto Web Mercator: visible reflectance as grey,
    infrared brightness temperature (kelvin) through the IR color table."""
    width, height = scan.size
    values, valid = _sample_scan(scan)
    alpha = np.where(valid, 255, 0).astype(np.uint8).reshape(height, width)
    if band == "ir":
        rgb = ir_rgb(np.where(valid, values, np.nan)).reshape(height, width, 3)
        return _encode_rgba(rgb, alpha)
    # gamma 0.5 to match the visual brightness of the live WMS tiles.
    grey = np.sqrt(np.clip(np.where(valid, values, 0.0), 0.0, 1.0))
    grey = (grey * 255.0 + 0.5).astype(np.uint8).reshape(height, width)
    return _encode_grey_alpha(grey, alpha)
