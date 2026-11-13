"""C18: lidar wind profiles (VAD / CSM winds) from the lidar truck and the
CLAMPS trailers: the shared THREDDS listing, parsing real files' quirks,
the session span and the fetcher that keeps its sets."""
import time
from datetime import datetime, timezone
from urllib.error import URLError

import pytest
from PyQt6.QtWidgets import QApplication

from archive.fetchers import clamps_common
from archive.fetchers import clamps_wind_archive_fetcher as wind
from core.vad import MS_TO_KNOT
from data import thredds_paths as paths
from tests.clamps_fakes import FakeThredds, catalog_html, not_found, wind_file


def _epoch(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def approx(value, rel=1e-4):
    return pytest.approx(value, rel=rel)


# -- clamps_common ------------------------------------------------------------------

def test_list_folder_reads_file_names_from_the_catalog_page(monkeypatch):
    folder = "FRDD/CLAMPS/clamps/clamps1/processed/clampsdlvadC1.c1"
    asked = []

    def read(kind, url, timeout=60):
        asked.append((kind, url))
        return catalog_html(folder, ["a.cdf", "b.cdf", "a.cdf"])

    monkeypatch.setattr(clamps_common, "read", read)
    assert clamps_common.list_folder(folder) == ["a.cdf", "b.cdf"]
    assert asked == [("listings", paths.catalog_url(folder))]
    assert clamps_common.list_folder(folder) == ["a.cdf", "b.cdf"]
    assert len(asked) == 1        # kept for the run


def test_list_folder_missing_is_empty_and_not_kept(monkeypatch):
    answers = [not_found(), catalog_html("F/x", ["a.cdf"])]

    def read(kind, url, timeout=60):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(clamps_common, "read", read)
    assert clamps_common.list_folder("F/x") == []
    assert clamps_common.list_folder("F/x") == ["a.cdf"]


def test_list_folder_raises_on_a_failed_request(monkeypatch):
    def read(kind, url, timeout=60):
        raise URLError("timed out")
    monkeypatch.setattr(clamps_common, "read", read)
    with pytest.raises(URLError):
        clamps_common.list_folder("F/x")


def test_read_goes_through_package_sources_with_the_nssl_user_agent(monkeypatch):
    from core import package_sources
    seen = []

    def read_url(kind, request, opener, **kwargs):
        seen.append((kind, request.full_url, request.get_header("User-agent"), opener))
        return b"data"

    monkeypatch.setattr(package_sources, "read_url", read_url)
    assert clamps_common.read("clamps profiles", "https://x/f.cdf") == b"data"
    kind, url, agent, opener = seen[0]
    assert (kind, url) == ("clamps profiles", "https://x/f.cdf")
    assert agent.startswith("Mozilla/5.0")
    from archive.fetchers.vehicle_obs_archive_fetcher import _urlopen_with_retry
    assert opener is _urlopen_with_retry


# -- parsing -------------------------------------------------------------------------

def test_parse_converts_units_and_time(tmp_path):
    data = wind_file(tmp_path, _epoch(2022, 5, 24), [3600.0], [0.1, 0.2],
                     wspd=[[10.0, 12.0]], wdir=[[270.0, 280.0]], rms=[[1.0, 1.5]], r_sq=[[0.9, 0.8]])
    (p,) = wind.parse_clamps_wind_netcdf(data, "CLAMPS1-VAD")
    assert p.timestamp == datetime(2022, 5, 24, 1, tzinfo=timezone.utc)
    assert p.site == "CLAMPS1-VAD"
    assert list(p.heights_m) == approx([100.0, 200.0])
    assert p.wind_spd[0] == approx(10.0 * MS_TO_KNOT)
    assert p.rms_error == approx(1.25 * MS_TO_KNOT)


def test_parse_drops_cells_flagged_bad_by_r_sq(tmp_path):
    """A real CLAMPS1 VAD file: bad cells are a finite sentinel in wspd and
    -999 in rms, with a large negative r_sq on the same cells."""
    data = wind_file(
        tmp_path, _epoch(2022, 5, 25), [0.0, 600.0], [0.1, 0.2, 0.3],
        wspd=[[5.0, 1412.7993, 6.0], [1412.7993] * 3],
        wdir=[[100.0, 999.0, 110.0], [999.0] * 3],
        rms=[[0.5, -999.0, 0.6], [-999.0] * 3],
        r_sq=[[0.9, -999.0, 0.8], [-999.0] * 3],
    )
    (p,) = wind.parse_clamps_wind_netcdf(data, "CLAMPS1-VAD")
    assert list(p.heights_m) == approx([100.0, 300.0])
    assert p.rms_error == approx(0.55 * MS_TO_KNOT)


def test_parse_drops_failed_fits_under_the_speed_bound(tmp_path):
    """The truck's DL1 VAD on 2026-05-17: r_sq in (-100, 0) at ~126 m/s."""
    data = wind_file(tmp_path, _epoch(2026, 5, 17), [72000.0], [0.005, 0.010, 0.015],
                     wspd=[[12.0, 125.8, 13.0]], wdir=[[200.0, 30.0, 205.0]], r_sq=[[0.97, -12.0, 0.96]])
    (p,) = wind.parse_clamps_wind_netcdf(data, "DLTRUCK1-DL1-VAD")
    assert list(p.heights_m) == approx([5.0, 15.0])


def test_parse_drops_the_default_fill_without_r_sq(tmp_path):
    data = wind_file(tmp_path, _epoch(2022, 5, 25), [0.0], [0.1, 0.2],
                     wspd=[[8.0, 5e9]], wdir=[[180.0, 180.0]], rms=[[9.96921e36, 9.96921e36]])
    (p,) = wind.parse_clamps_wind_netcdf(data, "DLTRUCK1-DL1-CSMWINDS")
    assert list(p.heights_m) == approx([100.0])
    assert p.rms_error == 0.0


def test_csm_winds_need_signal_and_a_small_uncertainty(tmp_path):
    """CSM fits to noise above the aerosol layer have r_sq near 1: intensity
    (SNR + 1) and wspd_sigma sort them out."""
    data = wind_file(
        tmp_path, _epoch(2022, 5, 19), [0.0], [0.1, 0.5, 6.0, 9.0],
        wspd=[[8.0, 12.0, 110.0, 15.0]], wdir=[[180.0] * 4], r_sq=[[0.99] * 4],
        intensity=[[1.05, 1.02, 1.003, 1.03]], wspd_sigma=[[0.5, 1.0, 0.8, 3.5]],
    )
    (p,) = wind.parse_clamps_wind_netcdf(data, "DLTRUCK1-DL2-CSMWINDS")
    assert list(p.heights_m) == approx([100.0, 500.0])


def test_vad_files_are_not_filtered_on_intensity(tmp_path):
    """A VAD averages a whole scan: CLAMPS2's good winds sit at 1.002."""
    data = wind_file(tmp_path, _epoch(2026, 5, 15), [0.0], [0.5, 1.0],
                     wspd=[[10.0, 12.0]], wdir=[[200.0, 210.0]], r_sq=[[0.999, 0.999]],
                     intensity=[[1.002, 1.001]])
    (p,) = wind.parse_clamps_wind_netcdf(data, "CLAMPS2-VAD")
    assert len(p.heights_m) == 2


def test_sources_and_names():
    dirs = {s.platform_dir for s in wind.KNOWN_CLAMPS_WIND_SOURCES}
    assert dirs == {"dltruck/dltruck1", "clamps/clamps1", "clamps/clamps2"}
    assert wind.wind_source_name("DLTRUCK1-DL1-VAD") == "Lidar truck VAD (DL1)"
    assert wind.wind_source_name("DLTRUCK1-DL2-CSMWINDS") == "Lidar truck CSM winds (DL2)"
    assert wind.wind_source_name("CLAMPS1-VAD") == "CLAMPS1 trailer VAD"


# -- the session's sets -------------------------------------------------------------

def _folder(platform_id):
    source = next(s for s in wind.KNOWN_CLAMPS_WIND_SOURCES if s.platform_id == platform_id)
    return source, paths.clamps_processed(source.platform_dir, source.datastream)


def test_files_are_found_by_listing_and_span_the_session(monkeypatch, tmp_path):
    fake = FakeThredds(monkeypatch)
    source, folder = _folder("CLAMPS1-VAD")
    day1 = wind_file(tmp_path, _epoch(2024, 4, 27), [64800.0, 0.0], [0.5],
                     wspd=[[10.0], [9.0]], wdir=[[200.0], [190.0]])
    fake.add(folder, f"{source.datastream}.20240427.000000.cdf", day1)
    # the next morning: 05Z is inside the session's cap, 07Z past it
    day2 = wind_file(tmp_path, _epoch(2024, 4, 28), [5 * 3600.0, 7 * 3600.0], [0.5],
                     wspd=[[11.0], [12.0]], wdir=[[210.0], [220.0]])
    fake.add(folder, f"{source.datastream}.20240428.000512.cdf", day2)
    fake.add(folder, f"{source.datastream}.20240426.000000.cdf", b"other day")
    fake.add(folder, f"clampsdlvadOTHER.c1.20240427.000000.cdf", b"another stream")

    sets, errors = wind.fetch_clamps_wind_sets(datetime(2024, 4, 27, 20, tzinfo=timezone.utc))
    assert errors == []
    assert list(sets) == ["CLAMPS1-VAD"]
    hours = [t.strftime("%d %H") for t in sets["CLAMPS1-VAD"].timestamps]
    assert hours == ["27 00", "27 18", "28 05"]
    assert not any("20240426" in u or "OTHER" in u for u in fake.file_requests())


def test_failures_are_reported_and_other_sources_still_load(monkeypatch, tmp_path):
    fake = FakeThredds(monkeypatch)
    source, folder = _folder("CLAMPS2-VAD")
    fake.add(folder, f"{source.datastream}.20240427.000000.cdf",
             wind_file(tmp_path, _epoch(2024, 4, 27), [0.0], [0.5], wspd=[[10.0]], wdir=[[200.0]]))
    bad_source, bad_folder = _folder("CLAMPS1-VAD")
    fake.folders[bad_folder] = []
    fake.broken_folders.add(bad_folder)
    sets, errors = wind.fetch_clamps_wind_sets(datetime(2024, 4, 27, 20, tzinfo=timezone.utc))
    assert list(sets) == ["CLAMPS2-VAD"]
    assert any(e.startswith("CLAMPS1-VAD:") for e in errors)


def _wait(app, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_fetcher_loads_once_and_keeps_the_sets(monkeypatch, app):
    calls = []
    sentinel = {"CLAMPS1-VAD": object()}
    monkeypatch.setattr(wind, "fetch_clamps_wind_sets", lambda t: calls.append(t) or (sentinel, []))
    fetcher = wind.ArchiveClampsWindFetcher(datetime(2024, 4, 27, 20, tzinfo=timezone.utc))
    got = []
    fetcher.sets_ready.connect(got.append)
    assert fetcher.sets is None
    assert fetcher.fetch()
    _wait(app, lambda: got)
    assert got == [sentinel] and fetcher.sets == sentinel
    assert fetcher.fetch()          # answered from what's kept
    assert len(got) == 2 and len(calls) == 1


def test_a_failed_load_reports_and_is_asked_again(monkeypatch, app):
    answers = [({}, ["CLAMPS1-VAD: timed out"]), ({}, [])]
    monkeypatch.setattr(wind, "fetch_clamps_wind_sets", lambda t: answers.pop(0))
    fetcher = wind.ArchiveClampsWindFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))
    errors, ready = [], []
    fetcher.error.connect(errors.append)
    fetcher.sets_ready.connect(ready.append)
    fetcher.fetch()
    _wait(app, lambda: errors)
    assert errors == ["CLAMPS1-VAD: timed out"] and fetcher.sets is None
    fetcher.fetch()
    _wait(app, lambda: ready)
    assert ready == [{}] and fetcher.sets == {}


def test_a_second_fetch_while_loading_is_refused(monkeypatch, app):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(wind, "fetch_clamps_wind_sets", lambda t: (gate.wait(5), ({}, []))[1])
    fetcher = wind.ArchiveClampsWindFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))
    ready = []
    fetcher.sets_ready.connect(ready.append)
    assert fetcher.fetch()
    assert fetcher.is_loading()
    assert not fetcher.fetch()
    gate.set()
    _wait(app, lambda: ready)
    assert ready == [{}]
