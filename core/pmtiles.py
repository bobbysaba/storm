"""Reading tiles from a PMTiles v3 file (https://github.com/protomaps/PMTiles,
spec/v3/spec.md): one file holding a tile set, found through a directory
of tile ids rather than an SQLite table as in MBTiles.

STORM downloads a contours source's .pmtiles file whole and serves its
tiles to the map from disk (ui/map/tile_scheme_handler.py). Directories and
the metadata may be gzipped or not; tiles too. Brotli and zstd aren't read.
"""
from __future__ import annotations

import gzip
import json
import struct
from bisect import bisect_right
from dataclasses import dataclass

_HEADER = struct.Struct("<7sB11Q6B4iB2i")
HEADER_LENGTH = _HEADER.size   # 127

# compression codes in the header
_NONE, _GZIP = 1, 2
_COMPRESSION_NAMES = {0: "unknown", 1: "none", 2: "gzip", 3: "brotli", 4: "zstd"}
# a directory leads to at most this many more (the spec allows 3 levels)
_MAX_DEPTH = 4


class PMTilesError(ValueError):
    """Not a PMTiles v3 file, or one STORM can't read."""


@dataclass(frozen=True)
class Header:
    root_offset: int
    root_length: int
    metadata_offset: int
    metadata_length: int
    leaf_offset: int
    leaf_length: int
    data_offset: int
    data_length: int
    internal_compression: int
    tile_compression: int
    tile_type: int
    min_zoom: int
    max_zoom: int
    # (west, south, east, north) in degrees
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class Entry:
    tile_id: int
    offset: int
    length: int
    # 0: the entry is a leaf directory, not tiles
    run_length: int


def read_header(data: bytes) -> Header:
    if len(data) < HEADER_LENGTH:
        raise PMTilesError("file is shorter than a PMTiles header")
    (magic, version, root_off, root_len, meta_off, meta_len, leaf_off, leaf_len,
     data_off, data_len, _addressed, _entries, _contents, _clustered, internal, tile_comp,
     tile_type, min_z, max_z, w, s, e, n, _center_z, _center_lon, _center_lat) = _HEADER.unpack_from(data)
    if magic != b"PMTiles":
        raise PMTilesError("not a PMTiles file")
    if version != 3:
        raise PMTilesError(f"PMTiles version {version} (STORM reads version 3)")
    return Header(root_off, root_len, meta_off, meta_len, leaf_off, leaf_len, data_off, data_len,
                  internal, tile_comp, tile_type, min_z, max_z,
                  (w / 1e7, s / 1e7, e / 1e7, n / 1e7))


def decompress(data: bytes, compression: int) -> bytes:
    if compression in (_NONE, 0):
        return data
    if compression == _GZIP:
        return gzip.decompress(data)
    raise PMTilesError(f"{_COMPRESSION_NAMES.get(compression, compression)} compression isn't supported")


def _varints(data: bytes):
    value = shift = 0
    for byte in data:
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
        else:
            yield value
            value = shift = 0


def read_directory(data: bytes) -> list[Entry]:
    """A decompressed directory: entries sorted by tile id."""
    values = _varints(data)
    try:
        count = next(values)
        ids, last = [], 0
        for _ in range(count):
            last += next(values)
            ids.append(last)
        runs = [next(values) for _ in range(count)]
        lengths = [next(values) for _ in range(count)]
        offsets = []
        for i in range(count):
            raw = next(values)
            # 0 after the first entry: right after the previous one
            offsets.append(offsets[i - 1] + lengths[i - 1] if raw == 0 and i > 0 else raw - 1)
    except StopIteration:
        raise PMTilesError("directory ends early") from None
    return [Entry(*fields) for fields in zip(ids, offsets, lengths, runs)]


def tile_id(z: int, x: int, y: int) -> int:
    """The tile's position along the Hilbert curve, counting every tile of
    the zooms below it first."""
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise ValueError(f"tile {z}/{x}/{y} is outside zoom {z}")
    acc = ((1 << (2 * z)) - 1) // 3   # 4^0 + 4^1 + ... + 4^(z-1)
    d = 0
    s = n >> 1
    while s:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = n - 1 - x, n - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def find(entries: list[Entry], tid: int) -> Entry | None:
    """The entry holding *tid*: its tiles, or the leaf directory to look in."""
    i = bisect_right([e.tile_id for e in entries], tid) - 1
    if i < 0:
        return None
    entry = entries[i]
    if entry.run_length == 0 or tid < entry.tile_id + entry.run_length:
        return entry
    return None


class PMTilesFile:
    """A PMTiles file on disk. The header and root directory are read once;
    each tile opens the file again, so one object serves any thread."""

    def __init__(self, path: str):
        self.path = str(path)
        with open(self.path, "rb") as f:
            self.header = read_header(f.read(HEADER_LENGTH))
        self._root = self._directory(self.header.root_offset, self.header.root_length)
        self._leaves: dict[int, list[Entry]] = {}

    def _read(self, offset: int, length: int) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        if len(data) != length:
            raise PMTilesError("file ends early")
        return data

    def _directory(self, offset: int, length: int) -> list[Entry]:
        return read_directory(decompress(self._read(offset, length), self.header.internal_compression))

    def metadata(self) -> dict:
        """The JSON metadata (vector_layers names the layers), or {}."""
        if not self.header.metadata_length:
            return {}
        raw = decompress(self._read(self.header.metadata_offset, self.header.metadata_length),
                         self.header.internal_compression)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        """The tile's bytes, decompressed; None when the file has no such tile."""
        if not self.header.min_zoom <= z <= self.header.max_zoom:
            return None
        try:
            tid = tile_id(z, x, y)
        except ValueError:
            return None
        entries = self._root
        for _ in range(_MAX_DEPTH):
            entry = find(entries, tid)
            if entry is None:
                return None
            if entry.run_length:
                data = self._read(self.header.data_offset + entry.offset, entry.length)
                return decompress(data, self.header.tile_compression)
            offset = self.header.leaf_offset + entry.offset
            entries = self._leaves.get(offset)
            if entries is None:
                entries = self._leaves[offset] = self._directory(offset, entry.length)
        raise PMTilesError("directories nest too deep")
