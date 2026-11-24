"""The FOFS file index (providers/nssl/indexes.py FOFS) follows whatever
layout NSSL's dates.json publishes."""

import json
from datetime import date

import pytest

from providers.nssl import indexes

_PUBLISHED = {
    "generated": "2026-11-21T16:00:00Z",
    "vehicles": {"probe1": ["20230601", "20240427"], "newtruck": ["20250605"]},
    "files": {
        "probe1": {
            "20240427": ["probe1/raw/20240427.txt"],
            "20230601": ["_legacy_data/probe1/20230601.txt"],
        },
        "newtruck": {"20250605": ["newtruck/raw/20250605.txt"]},
    },
}


def parse_index(data: dict) -> list[indexes.IndexedFile]:
    return indexes.parse_index(data, indexes.FOFS.root, "FOFS index", indexes.FOFS_VEHICLE)


def index_of(files: dict) -> indexes.Index:
    """An index as if NSSL published {"files": files}; for other tests too."""
    return indexes.Index(parse_index({"files": files}), 0.0)


def _real_index() -> indexes.PublishedIndex:
    # conftest stubs FOFS.get out for every test; exercise a real one like it
    return indexes.PublishedIndex("fofs", indexes.FOFS_INDEX_PATH, kind="mesonet",
                                  platform_pattern=indexes.FOFS_VEHICLE, require_files=True)


def test_the_index_lists_every_file_under_its_vehicle_and_day():
    files = parse_index(_PUBLISHED)

    assert {(f.platform, f.date, f.path) for f in files} == {
        ("probe1", date(2024, 4, 27), "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt"),
        ("probe1", date(2023, 6, 1), "FOFS/Mobile-Mesonet/data/_legacy_data/probe1/20230601.txt"),
        ("newtruck", date(2025, 6, 5), "FOFS/Mobile-Mesonet/data/newtruck/raw/20250605.txt"),
    }
    assert files[0].url.startswith("https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/")


def test_entries_it_cant_read_are_skipped_not_fatal():
    files = parse_index({"files": {
        "Probe1": {"20240427": ["/probe1/raw/20240427.txt"]},     # case and a leading slash: fine
        "bad name!": {"20240427": ["x.txt"]},
        "probe2": {"2024-04-27": ["probe2/x.txt"], "20240428": ["../etc/passwd", ""]},
    }})
    assert [(f.platform, f.path) for f in files] == [("probe1", "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt")]


@pytest.mark.parametrize("data", [[], {}, {"files": []}, {"vehicles": {"probe1": ["20240427"]}}])
def test_something_that_isnt_an_index_is_an_error(data):
    with pytest.raises(ValueError):
        parse_index(data)


def test_index_is_cached_and_a_missing_or_broken_one_never_replaces_it():
    index = _real_index()
    clock = [1000.0]

    first = index.get(lambda: json.dumps(_PUBLISHED), now=lambda: clock[0])
    assert len(first.files) == 3 and index.cache_path.exists()

    clock[0] += indexes.CACHE_TTL_S + 1
    index._current = None
    stale = index.get(lambda: None, now=lambda: clock[0])      # unpublished
    assert stale is not None and len(stale.files) == 3

    def broken():
        raise TimeoutError("waf")
    index._current, index._failed_at = None, None
    assert len(index.get(broken, now=lambda: clock[0]).files) == 3

    index._current, index._failed_at = None, None
    empty = index.get(lambda: '{"files": {}}', now=lambda: clock[0])
    assert len(empty.files) == 3


def test_an_unpublished_index_is_asked_for_once_per_retry_window():
    index = _real_index()
    clock = [1000.0]
    asked = []

    def unpublished():
        asked.append(clock[0])
        return None

    for _ in range(5):
        assert index.get(unpublished, now=lambda: clock[0]) is None
    assert len(asked) == 1

    clock[0] += indexes.RETRY_AFTER_S + 1
    index.get(lambda: json.dumps(_PUBLISHED), now=lambda: clock[0])
    assert index._failed_at is None


_MANIFEST = [   # NSSL's hourly data/manifest.json
    {"filename": "20240427.txt", "category": "probe1", "stage": "raw", "path": "probe1/raw/20240427.txt"},
    {"filename": "20240427.txt", "category": "_legacy_data", "stage": "probe1",
     "path": "_legacy_data/probe1/20240427.txt"},
    {"filename": "20240427_support.txt", "category": "_legacy_data", "stage": "probe1",
     "path": "_legacy_data/probe1/20240427_support.txt"},
    {"filename": "notes.txt", "category": "probe1", "stage": "raw", "path": "probe1/raw/notes.txt"},
]


def test_the_hourly_manifest_list_reads_vehicle_and_day_from_each_path():
    files = parse_index(_MANIFEST)
    assert [(f.platform, f.date, f.path) for f in files] == [
        ("probe1", date(2024, 4, 27), "FOFS/Mobile-Mesonet/data/_legacy_data/probe1/20240427.txt"),
        ("probe1", date(2024, 4, 27), "FOFS/Mobile-Mesonet/data/_legacy_data/probe1/20240427_support.txt"),
        ("probe1", date(2024, 4, 27), "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt"),
    ]
    wrapped = parse_index({"generated": "2026-11-24T17:00:00Z", "files": _MANIFEST})
    assert wrapped == files


def test_a_manifest_entry_can_name_its_platform_and_date():
    files = parse_index([{"path": "x/launch.txt", "platform": "probe2", "date": "20240428"}])
    assert [(f.platform, f.date) for f in files] == [("probe2", date(2024, 4, 28))]


def test_the_fofs_index_is_re_read_after_an_hour():
    index = _real_index()
    assert index.index_path == "FOFS/Mobile-Mesonet/data/manifest.json"
    assert indexes.FOFS.max_age_s == indexes.FOFS_CACHE_TTL_S == 3600
    clock = [1000.0]
    index.max_age_s = indexes.FOFS_CACHE_TTL_S
    index.get(lambda: json.dumps(_MANIFEST), now=lambda: clock[0])
    clock[0] += indexes.FOFS_CACHE_TTL_S + 1
    later = [{"path": "probe1/raw/20240428.txt"}]
    assert index.get(lambda: json.dumps(later), now=lambda: clock[0]).dates("probe1") == {date(2024, 4, 28)}
