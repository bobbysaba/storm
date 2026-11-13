import sqlite3

from core import mbtiles


def _make_tiles(path, bounds="-110.5,30.25,-90,45"):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    if bounds is not None:
        conn.execute("INSERT INTO metadata VALUES ('bounds', ?)", (bounds,))
    conn.commit()
    conn.close()


def test_bounds_of_a_missing_file_does_not_create_it(tmp_path):
    path = tmp_path / "storm.mbtiles"
    assert mbtiles.bounds(str(path)) is None
    assert not path.exists()


def test_connect_refuses_to_create_a_missing_file(tmp_path):
    path = tmp_path / "storm.mbtiles"
    try:
        mbtiles.connect(str(path))
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("connect() opened a file that doesn't exist")
    assert not path.exists()


def test_empty_file_is_not_usable(tmp_path):
    path = tmp_path / "storm.mbtiles"
    path.write_bytes(b"")
    assert not mbtiles.usable(str(path))
    assert mbtiles.bounds(str(path)) is None


def test_bounds_reads_metadata(tmp_path):
    path = tmp_path / "with space.mbtiles"      # the path is URI-quoted
    _make_tiles(path)
    assert mbtiles.usable(str(path))
    assert mbtiles.bounds(str(path)) == (-110.5, 30.25, -90.0, 45.0)


def test_bounds_none_without_or_with_bad_metadata(tmp_path):
    missing = tmp_path / "a.mbtiles"
    _make_tiles(missing, bounds=None)
    assert mbtiles.bounds(str(missing)) is None
    bad = tmp_path / "b.mbtiles"
    _make_tiles(bad, bounds="not,numbers")
    assert mbtiles.bounds(str(bad)) is None


def test_connect_is_read_only(tmp_path):
    path = tmp_path / "storm.mbtiles"
    _make_tiles(path)
    conn = mbtiles.connect(str(path))
    try:
        conn.execute("INSERT INTO metadata VALUES ('x', 'y')")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("connection was writable")
    finally:
        conn.close()
