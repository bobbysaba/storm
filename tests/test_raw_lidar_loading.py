"""Raw lidar downloads to the disk cache (archive/fetchers/clamps_common.py)
and the background survey (archive/fetchers/raw_lidar_quicklook_fetcher.py)."""
import io
import os
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from archive.fetchers import clamps_common
from archive.fetchers import raw_lidar_quicklook_fetcher as qf
from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from core import package_sources, provenance

URL = "https://data.nssl.noaa.gov/thredds/fileServer/x/clampsdlppiC1.b1.20230609.120000.cdf"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def network(monkeypatch):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return _Response(b"x" * 1000)
    monkeypatch.setattr(clamps_common, "_urlopen_with_retry", opener)
    provenance.reset(active=True)
    yield calls
    provenance.reset(active=False)


def test_a_download_is_kept_and_reused_while_its_hash_matches(network, tmp_path):
    path = clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf")
    assert path.read_bytes() == b"x" * 1000 and path.suffix == ".cdf"
    assert clamps_common.is_cached(URL, tmp_path, "f.cdf")
    [entry] = provenance.snapshot()
    assert entry["bytes"] == 1000 and entry["kind"] == "raw lidar" and entry["sha256"]
    assert clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf") == path
    assert len(network) == 1
    path.write_bytes(b"changed")                         # a damaged copy is downloaded again
    clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf")
    assert len(network) == 2 and path.read_bytes() == b"x" * 1000
    assert not list(tmp_path.glob("*.part"))


def test_a_case_package_copy_comes_first(network, monkeypatch, tmp_path):
    monkeypatch.setattr(package_sources, "get", lambda url: b"packaged" if url == URL else None)
    path = clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf")
    assert path.read_bytes() == b"packaged" and network == []


def test_cancelled_and_oversized_downloads_leave_nothing(network, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(clamps_common.Cancelled):
        clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf", cancel=cancel)
    with pytest.raises(OSError, match="over"):
        clamps_common.download_to_cache("raw lidar", URL, tmp_path, "f.cdf", max_bytes=100)
    assert not list(tmp_path.iterdir())


def test_the_cache_is_pruned_least_recently_used_first(tmp_path):
    for i, name in enumerate(("old", "mid", "new")):
        data, record = tmp_path / f"{name}.cdf", tmp_path / f"{name}.cdf.json"
        data.write_bytes(b"x" * 100)
        record.write_text("{}")
        os.utime(data, (1000 + i, 1000 + i))
    stale = tmp_path / "abc.part"
    stale.write_bytes(b"x")
    os.utime(stale, (1000, 1000))
    (tmp_path / "fresh.part").write_bytes(b"x")
    freed = clamps_common.prune_cache(tmp_path, 250)
    assert freed > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fresh.part", "mid.cdf", "mid.cdf.json",
                                                         "new.cdf", "new.cdf.json"]


# -- the background survey -------------------------------------------------------------

def _asset(product="ppi", mobile=False, name="f.cdf"):
    source = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.product == product and s.mobile == mobile)
    return LidarAsset(source, name)


def _wait(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        time.sleep(0.01)
    assert condition()


def test_discovery_reports_each_source_and_the_trailer_sites(monkeypatch, tmp_path):
    monkeypatch.setattr(qf, "RAW_LIDAR_CACHE_DIR", tmp_path)
    found = {"CLAMPS1-PPI": [_asset()]}

    def discover(source, day):
        if source.platform_id == "CLAMPS2-PPI":
            raise OSError("down")
        return found.get(source.platform_id, []) if day.day == 9 else []
    monkeypatch.setattr(qf, "discover_raw_lidar", discover)
    monkeypatch.setattr(qf, "read_site", lambda asset: {"lat": 35.0, "lon": -97.0})
    fetcher = qf.ArchiveRawLidarFetcher(datetime(2023, 6, 9, 20, tzinfo=timezone.utc))
    assets, sites, errors = [], [], []
    fetcher.assets_ready.connect(assets.append)
    fetcher.sites_ready.connect(sites.append)
    fetcher.error.connect(errors.append)
    fetcher._do_fetch()
    assert assets == [{"CLAMPS1-PPI": found["CLAMPS1-PPI"], "CLAMPS2-PPI": None}]
    assert sites == [{"CLAMPS1": {"lat": 35.0, "lon": -97.0}}]
    assert "CLAMPS2-PPI: down" in errors[0]


def test_the_survey_holds_back_large_files_and_drops_scan_less_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(qf, "RAW_LIDAR_CACHE_DIR", tmp_path)
    monkeypatch.setattr(qf, "_remote_size", lambda url: 500e6 if "big" in url else 1e6)
    big, vad, ppi, csm = (_asset(name="big.cdf"), _asset(name="vad.cdf"), _asset(name="ppi.cdf"),
                          _asset("csm", name="csm.cdf"))
    loaded = []

    def load(asset, cache_dir, cancel=None, truck_track=None):
        loaded.append(asset.filename)
        rays = SimpleNamespace(name=asset.filename)
        return rays
    monkeypatch.setattr(qf, "load_raw_lidar", load)
    monkeypatch.setattr("core.lidar_scans.classify_scans",
                        lambda rays: [] if rays.name == "vad.cdf" else ["scan"])
    fetcher = qf.ArchiveRawLidarFetcher(datetime(2023, 6, 9, 20, tzinfo=timezone.utc))
    files, large, done = [], [], []
    fetcher.file_ready.connect(lambda i, rays: files.append(rays.name))
    fetcher.large_file.connect(lambda i, asset, size: large.append(asset.filename))
    fetcher.instrument_loaded.connect(lambda i, n: done.append(n))
    fetcher._load_files("CLAMPS1", [csm, big, vad, ppi], force=False)
    assert loaded == ["ppi.cdf", "vad.cdf", "csm.cdf"]          # PPI first; the big one held back
    assert files == ["ppi.cdf", "csm.cdf"] and large == ["big.cdf"] and done == [2]
    fetcher._load_files("CLAMPS1", [big], force=True)            # once picked: loaded whatever its size
    assert loaded[-1] == "big.cdf"


def test_the_truck_track_is_built_once_and_cancel_stops_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(qf, "RAW_LIDAR_CACHE_DIR", tmp_path)
    monkeypatch.setattr(qf, "_remote_size", lambda url: 1)
    tracks = []
    monkeypatch.setattr(qf, "load_raw_lidar",
                        lambda asset, cache_dir, cancel=None, truck_track=None: tracks.append(truck_track)
                        or SimpleNamespace())
    monkeypatch.setattr("core.lidar_scans.classify_scans", lambda rays: ["scan"])
    asked = []
    fetcher = qf.ArchiveRawLidarFetcher(datetime(2023, 6, 9, 20, tzinfo=timezone.utc))
    fetcher.set_truck_track(lambda: asked.append(1) or [])
    fetcher._load_files("DLTRUCK1", [_asset(mobile=True, name="a.cdf"), _asset(mobile=True, name="b.cdf")],
                        force=False)
    assert len(asked) == 1 and tracks[0] is tracks[1] and len(tracks[0]) == 0
    fetcher.cancel()
    done = []
    fetcher.instrument_loaded.connect(lambda i, n: done.append(n))
    fetcher._load_files("DLTRUCK1", [_asset(mobile=True, name="c.cdf")], force=True)
    assert len(tracks) == 2 and done == []
