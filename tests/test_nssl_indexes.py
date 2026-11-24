"""providers/nssl/indexes.py: NSSL's published file indexes, and the
radiosonde and TROPoe fetchers finding files through them (or by listing
folders until an index is published)."""
import json
from datetime import date, datetime, timezone

import pytest

from providers.nssl import indexes
from providers.nssl.archive import clamps_common, clamps_sonde_archive_fetcher as sondes
from providers.nssl.archive import clamps_tropoe_archive_fetcher as tropoe

INDEX = {"generated": "2026-11-21T16:00:00Z", "files": {
    "dltruck": {"20240427": ["sonde/upperair.NSSL_Lidar_sonde.202404272130.skewT.text",
                             "sonde/upperair.NSSL_Lidar_sonde.202404272300.skewT.text"]},
    "CLAMPS2": {"20240427": ["c2/clampstropoe10.aeri_mwr.v1.C2.20240427.000510.nc",
                             "c2/clampstropoe10.mwr_only.v1.C2.20240427.000510.nc"]},
    "bad": "not a table",
}}


def _published(name="x", path="FRDD/STORM/index.json"):
    return indexes.PublishedIndex(name, path)


def test_parse_index_reads_paths_under_the_index_folder():
    files = indexes.parse_index(INDEX, "FRDD/STORM")
    assert [f.platform for f in files] == ["CLAMPS2", "CLAMPS2", "dltruck", "dltruck"]
    assert files[2].path == "FRDD/STORM/sonde/upperair.NSSL_Lidar_sonde.202404272130.skewT.text"
    assert files[2].date == date(2024, 4, 27)
    with pytest.raises(ValueError, match="no \"files\" table"):
        indexes.parse_index({"vehicles": {}}, "")


def test_an_unpublished_index_asks_nothing():
    idx = indexes.PublishedIndex("sondes", "")
    assert idx.get(lambda: pytest.fail("asked")) is None


def test_a_fetched_index_is_cached_and_a_failure_keeps_the_copy():
    clock = [1000.0]
    idx = _published()
    got = idx.get(lambda: json.dumps(INDEX), now=lambda: clock[0])
    assert got.platforms() == {"dltruck", "CLAMPS2"}
    assert idx.cache_path.is_file()
    # a new process (no memory copy), the cache gone stale, the server down
    clock[0] += indexes.CACHE_TTL_S + 1
    again = _published()

    def down():
        raise TimeoutError("down")
    stale = again.get(down, now=lambda: clock[0])
    assert stale is not None and stale.dates("dltruck") == {date(2024, 4, 27)}


def test_radiosondes_come_from_the_index_when_published(monkeypatch):
    idx = _published("sondes")
    idx.get(lambda: json.dumps(INDEX))
    monkeypatch.setattr(indexes, "SONDES", idx)
    monkeypatch.setattr(clamps_common, "list_folder", lambda path: pytest.fail("listed"))
    found = sondes.sonde_files(datetime(2024, 4, 27, 23, tzinfo=timezone.utc))
    assert [n for n, _ in found] == ["upperair.NSSL_Lidar_sonde.202404272130.skewT.text",
                                    "upperair.NSSL_Lidar_sonde.202404272300.skewT.text"]
    assert found[0][1].endswith("/FRDD/STORM/sonde/upperair.NSSL_Lidar_sonde.202404272130.skewT.text")


def test_radiosondes_list_the_folder_until_the_index_exists(monkeypatch):
    monkeypatch.setattr(indexes, "SONDES", indexes.PublishedIndex("sondes", ""))
    monkeypatch.setattr(clamps_common, "list_folder", lambda path: [
        "upperair.NSSL_Lidar_sonde.202404272130.skewT.text", "upperair.NSSL_Lidar_sonde.202404281200.skewT.text"])
    found = sondes.sonde_files(datetime(2024, 4, 27, tzinfo=timezone.utc))
    assert [n for n, _ in found] == ["upperair.NSSL_Lidar_sonde.202404272130.skewT.text"]


def test_tropoe_takes_the_first_variant_the_index_has(monkeypatch):
    idx = _published("tropoe")
    idx.get(lambda: json.dumps(INDEX))
    monkeypatch.setattr(indexes, "TROPOE", idx)
    monkeypatch.setattr(clamps_common, "list_folder", lambda path: pytest.fail("listed"))
    c2 = next(p for p in tropoe.KNOWN_CLAMPS_TROPOE_PLATFORMS if p.platform_id == "CLAMPS2")
    c1 = next(p for p in tropoe.KNOWN_CLAMPS_TROPOE_PLATFORMS if p.platform_id == "CLAMPS1")
    stream, urls = tropoe._files_for_date(c2, "20240427")
    assert stream == "clampstropoe10.aeri_mwr.v1.C2" and len(urls) == 1
    assert tropoe._files_for_date(c1, "20240427") is None
