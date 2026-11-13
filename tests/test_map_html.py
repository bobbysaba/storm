import sqlite3

import ui.map.html as html


def _tiles(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.commit()
    conn.close()


def test_local_tiles_are_served_by_storm_scheme(tmp_path, monkeypatch):
    path = tmp_path / "storm.mbtiles"
    _tiles(path)
    monkeypatch.setattr(html, "TILES_PATH", str(path))
    assert html.basemap_source() == "local"
    page = html.build_map_html()
    assert 'tiles: ["storm://app/tiles/{z}/{x}/{y}.pbf"],' in page
    assert html.ONLINE_TILEJSON not in page
    assert "__TILE_URL__" not in page


def test_missing_tiles_use_the_online_base_map(tmp_path, monkeypatch):
    path = tmp_path / "storm.mbtiles"
    monkeypatch.setattr(html, "TILES_PATH", str(path))
    assert html.basemap_source() == "online"
    page = html.build_map_html()
    assert f'url: "{html.ONLINE_TILEJSON}",' in page
    assert "__TILE_URL__" not in page
    assert not path.exists()            # checking never creates the file


def test_empty_tiles_file_counts_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "storm.mbtiles"
    path.write_bytes(b"")
    monkeypatch.setattr(html, "TILES_PATH", str(path))
    assert html.basemap_source() == "online"
