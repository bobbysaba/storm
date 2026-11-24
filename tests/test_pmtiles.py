"""core/pmtiles.py: the PMTiles v3 reader, against files written here to the spec."""

import gzip
import json
import struct

import pytest

from core import pmtiles


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _directory(entries) -> bytes:
    """entries: (tile_id, offset, length, run_length), sorted by tile id."""
    out = _varint(len(entries))
    last = 0
    for tid, *_ in entries:
        out += _varint(tid - last)
        last = tid
    for *_, run in entries:
        out += _varint(run)
    for _, _, length, _ in entries:
        out += _varint(length)
    for i, (_, offset, _, _) in enumerate(entries):
        prev = entries[i - 1] if i else None
        contiguous = prev is not None and offset == prev[1] + prev[2]
        out += _varint(0 if contiguous else offset + 1)
    return out


def write(path, tiles: dict, *, metadata=None, gzip_dirs=True, gzip_tiles=True,
          leaf_size=None, zooms=None, bounds=(-105.0, 30.0, -90.0, 40.0)):
    """A PMTiles file of *tiles* ({(z, x, y): bytes}); with *leaf_size*, the
    root points at leaf directories of that many entries."""
    pack = gzip.compress if gzip_dirs else (lambda b: b)
    data, entries = b"", []
    for tid, payload in sorted((pmtiles.tile_id(*k), v) for k, v in tiles.items()):
        blob = gzip.compress(payload) if gzip_tiles else payload
        entries.append((tid, len(data), len(blob), 1))
        data += blob
    leaves = b""
    if leaf_size:
        root = []
        for i in range(0, len(entries), leaf_size):
            chunk = pack(_directory(entries[i:i + leaf_size]))
            root.append((entries[i][0], len(leaves), len(chunk), 0))
            leaves += chunk
        root_dir = pack(_directory(root))
    else:
        root_dir = pack(_directory(entries))
    meta = pack(json.dumps(metadata or {}).encode())
    zs = [z for z, _, _ in tiles] or [0]
    min_z, max_z = zooms or (min(zs), max(zs))
    root_off = pmtiles.HEADER_LENGTH
    meta_off = root_off + len(root_dir)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    comp = 2 if gzip_dirs else 1
    header = struct.pack(
        "<7sB11Q6B4iB2i", b"PMTiles", 3, root_off, len(root_dir), meta_off, len(meta),
        leaf_off, len(leaves), data_off, len(data), len(tiles), len(entries), len(entries),
        1, comp, 2 if gzip_tiles else 1, 1, min_z, max_z,
        *(round(v * 1e7) for v in bounds), min_z, 0, 0)
    path.write_bytes(header + root_dir + meta + leaves + data)
    return path


def test_tile_ids_follow_the_spec():
    assert pmtiles.tile_id(0, 0, 0) == 0
    # zoom 1 runs (0,0), (0,1), (1,1), (1,0)
    assert [pmtiles.tile_id(1, x, y) for x, y in ((0, 0), (0, 1), (1, 1), (1, 0))] == [1, 2, 3, 4]
    for z in range(1, 6):
        n = 1 << z
        first = sum(4 ** i for i in range(z))
        by_id = {pmtiles.tile_id(z, x, y): (x, y) for x in range(n) for y in range(n)}
        # every tile of the zoom, numbered after the zooms below it
        assert sorted(by_id) == list(range(first, first + n * n))
        # a Hilbert curve: each next tile shares an edge with the one before
        for tid in range(first, first + n * n - 1):
            (x0, y0), (x1, y1) = by_id[tid], by_id[tid + 1]
            assert abs(x0 - x1) + abs(y0 - y1) == 1
    with pytest.raises(ValueError):
        pmtiles.tile_id(2, 4, 0)


@pytest.mark.parametrize("options", [
    {},
    {"gzip_dirs": False, "gzip_tiles": False},
    {"leaf_size": 3},
])
def test_tiles_are_read_back(tmp_path, options):
    tiles = {(z, x, y): f"{z}/{x}/{y}".encode() for z in (3, 4) for x in range(3, 6) for y in range(5, 7)}
    path = write(tmp_path / "t.pmtiles", tiles, metadata={"vector_layers": [{"id": "tmpc"}]}, **options)
    f = pmtiles.PMTilesFile(path)
    for key, payload in tiles.items():
        assert f.tile(*key) == payload
    assert f.tile(3, 0, 0) is None            # not in the file
    assert f.tile(9, 100, 200) is None        # outside its zooms
    assert f.metadata()["vector_layers"][0]["id"] == "tmpc"
    assert (f.header.min_zoom, f.header.max_zoom) == (3, 4)
    assert f.header.bounds == pytest.approx((-105.0, 30.0, -90.0, 40.0))


def test_a_run_of_identical_tiles_shares_one_entry(tmp_path):
    path = write(tmp_path / "t.pmtiles", {(2, 0, 0): b"x"}, zooms=(2, 2))
    raw = bytearray(path.read_bytes())
    f = pmtiles.PMTilesFile(path)
    # stretch the only entry over the next two tile ids
    entries = [pmtiles.Entry(5, 0, f._root[0].length, 3)]
    root = gzip.compress(_directory([(e.tile_id, e.offset, e.length, e.run_length) for e in entries]))
    assert len(root) <= f.header.root_length
    raw[pmtiles.HEADER_LENGTH:pmtiles.HEADER_LENGTH + len(root)] = root
    struct.pack_into("<Q", raw, 16, len(root))
    path.write_bytes(bytes(raw))
    f = pmtiles.PMTilesFile(path)
    by_id = {pmtiles.tile_id(2, x, y): f.tile(2, x, y) for x in range(4) for y in range(4)}
    assert [by_id[tid] for tid in range(5, 9)] == [b"x", b"x", b"x", None]


def test_not_pmtiles_is_an_error(tmp_path):
    (tmp_path / "x.pmtiles").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(pmtiles.PMTilesError, match="not a PMTiles"):
        pmtiles.PMTilesFile(tmp_path / "x.pmtiles")
    with pytest.raises(pmtiles.PMTilesError, match="shorter"):
        pmtiles.read_header(b"PMTiles")
