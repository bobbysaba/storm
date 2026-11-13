"""C18 / C21: the archive sounding viewer's profile sources -- lidar truck
sondes on THREDDS, CLAMPS TROPoe retrievals, PERiLS CopterSonde ascents --
and the NSSL provider chain that tries them in turn."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from archive.fetchers import clamps_sonde_archive_fetcher as sonde
from archive.fetchers import clamps_tropoe_archive_fetcher as tropoe
from archive.fetchers import coptersonde_archive_fetcher as copter
from archive.fetchers import sounding_archive_fetcher as archive
from core.observation import Observation
from core.sounding import Sounding, SoundingSet
from data import thredds_paths as paths
from data.fetchers import clamps_sounding_fetcher as live
from tests.clamps_fakes import SKEWT, FakeThredds, coptersonde_file, tropoe_file

UTC = timezone.utc


def _epoch(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp())


# -- TROPoe ------------------------------------------------------------------------------

def test_tropoe_maps_fields_and_height_to_msl(tmp_path):
    data = tropoe_file(tmp_path, _epoch(2022, 5, 25), [1200.0], [0.0, 0.1, 1.0],
                       temperature=[[25.0, 24.0, 18.0]], dewpt=[[20.0, 19.0, 12.0]],
                       pressure=[[1005.0, 995.0, 900.0]], qc_flag=[0], converged_flag=[1], alt=20.0)
    (s,) = tropoe.parse_clamps_tropoe_netcdf(data, "CLAMPS2")
    assert s.valid_time == datetime(2022, 5, 25, 0, 20, tzinfo=UTC)
    assert list(s.height) == pytest.approx([20.0, 120.0, 1020.0])
    assert list(s.temperature) == [25.0, 24.0, 18.0]
    assert list(s.pressure) == [1005.0, 995.0, 900.0]
    assert np.isnan(s.u_wind).all() and np.isnan(s.v_wind).all()      # absent, not calm


def test_tropoe_drops_unconverged_and_qc_failed_rows(tmp_path):
    """A rejected retrieval still has plausible values (a flat profile)."""
    data = tropoe_file(tmp_path, _epoch(2022, 5, 25), [0.0, 600.0, 1200.0], [0.0, 1.0],
                       temperature=[[20.0, 15.0], [25.0, 25.0], [21.0, 16.0]],
                       dewpt=[[15.0, 10.0], [25.0, 25.0], [16.0, 11.0]],
                       pressure=[[1000.0, 900.0]] * 3, qc_flag=[0, 2, 0], converged_flag=[1, 1, 2])
    soundings = tropoe.parse_clamps_tropoe_netcdf(data, "CLAMPS2")
    assert [list(s.temperature) for s in soundings] == [[20.0, 15.0]]


def _tropoe_folder(unit_dir, unit, variant):
    stream = paths.clamps_tropoe_stream(variant, unit)
    return stream, paths.clamps_processed(unit_dir, stream)


def _one_retrieval(tmp_path, hour, name):
    return tropoe_file(tmp_path, _epoch(2022, 3, 1), [hour * 3600.0], [0.0, 1.0],
                       temperature=[[10.0, 5.0]], dewpt=[[5.0, 0.0]], pressure=[[1000.0, 890.0]],
                       qc_flag=[0], converged_flag=[1], name=name)


def test_tropoe_takes_the_preferred_variant_and_all_its_files(monkeypatch, tmp_path):
    fake = FakeThredds(monkeypatch)
    stream, folder = _tropoe_folder(paths.CLAMPS1, "C1", "aeri.v2")
    fake.add(folder, f"{stream}.20220301.001005.nc", _one_retrieval(tmp_path, 1, "a.nc"))
    fake.add(folder, f"{stream}.20220301.120005.nc", _one_retrieval(tmp_path, 13, "b.nc"))
    # the folder also holds another stream's files (as on THREDDS)
    fake.add(folder, "clampstropoe10.mwr.v1.C1.20220301.001005.nc", b"not this stream")
    mwr_stream, mwr_folder = _tropoe_folder(paths.CLAMPS1, "C1", "mwr.v1")
    fake.add(mwr_folder, f"{mwr_stream}.20220301.001005.nc", b"a less preferred variant")

    sset = tropoe.fetch_clamps_tropoe_soundings(datetime(2022, 3, 1, 23, tzinfo=UTC))
    assert sset.source == "clamps_tropoe" and sset.is_clamps_tropoe
    assert sset.station_id == "CLAMPS1" and sset.station_name == "CLAMPS1 TROPoe (aeri.v2)"
    assert [s.valid_time.hour for s in sset.soundings] == [1, 13]
    assert [s.slot_offset for s in sset.soundings] == [0, 1]
    assert not any("mwr" in u for u in fake.file_requests())


def test_tropoe_none_when_no_trailer_has_the_date(monkeypatch):
    FakeThredds(monkeypatch)
    assert tropoe.fetch_clamps_tropoe_soundings(datetime(2022, 3, 1, tzinfo=UTC)) is None


def test_tropoe_raises_when_nothing_found_and_a_listing_failed(monkeypatch):
    fake = FakeThredds(monkeypatch)
    _, folder = _tropoe_folder(paths.CLAMPS1, "C1", paths.CLAMPS_TROPOE_VARIANTS[0])
    fake.broken_folders.add(folder)
    with pytest.raises(Exception, match="timed out"):
        tropoe.fetch_clamps_tropoe_soundings(datetime(2022, 3, 1, tzinfo=UTC))


# -- lidar truck sondes ----------------------------------------------------------------------

_SONDE_FOLDER = paths.clamps_ingested(paths.SONDE_PLATFORM, paths.SONDE_STREAM)


def _sondes(monkeypatch, *stamps):
    fake = FakeThredds(monkeypatch)
    for stamp in stamps:
        fake.add(_SONDE_FOLDER, f"upperair.NSSL_Lidar_sonde.{stamp}.skewT.text", SKEWT.encode())
    return fake


def test_sondes_are_listed_and_filtered_by_date(monkeypatch):
    fake = _sondes(monkeypatch, "202205240036", "202205241938", "202205250000")
    sset = sonde.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, 23, tzinfo=UTC))
    assert sset.source == "nssl" and sset.station_id == "CLAMPS"
    assert [s.valid_time.strftime("%H%M") for s in sset.soundings] == ["0036", "1938"]
    assert len(sset.soundings[0].pressure) == 5
    assert not any("202205250000" in u for u in fake.file_requests())


def test_no_sondes_that_day_is_none(monkeypatch):
    _sondes(monkeypatch, "202205250000")
    assert sonde.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, tzinfo=UTC)) is None


def test_launches_are_placed_on_the_trucks_track(monkeypatch):
    _sondes(monkeypatch, "202205240036")
    fix_time = datetime(2022, 5, 24, 0, 36, 20, tzinfo=UTC)
    asked = []

    def truck(when):
        asked.append(when)
        return 35.5, -97.4, fix_time

    sset = sonde.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, 23, tzinfo=UTC), truck)
    snd = sset.soundings[0]
    assert asked == [datetime(2022, 5, 24, 0, 36, tzinfo=UTC)]
    assert (snd.lat, snd.lon, snd.location_time) == (35.5, -97.4, fix_time)
    assert snd.location_source.startswith("lidar truck track")
    assert (sset.lat, sset.lon) == (35.5, -97.4)


def test_an_unplaceable_launch_is_marked_unknown(monkeypatch):
    _sondes(monkeypatch, "202205240036")
    sset = sonde.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, 23, tzinfo=UTC), lambda when: None)
    snd = sset.soundings[0]
    assert (snd.lat, snd.lon) == (0.0, 0.0) and snd.location_source == "unknown"


def test_nearest_fix_is_within_a_minute():
    from archive.fetchers.vehicle_obs_archive_fetcher import nearest_fix
    t = datetime(2022, 5, 24, 0, 36, tzinfo=UTC)
    track = [Observation("lid1", 35.0 + i, -97.0, t + timedelta(seconds=40 * i)) for i in range(-3, 4)]
    assert nearest_fix(track, t + timedelta(seconds=15)).lat == 35.0
    assert nearest_fix(track, t + timedelta(seconds=25)).lat == 36.0
    assert nearest_fix(track, t + timedelta(seconds=220)) is None
    assert nearest_fix(track, t - timedelta(seconds=181)) is None
    assert nearest_fix([], t) is None


# -- CopterSonde ---------------------------------------------------------------------------

def _ascent(tmp_path, start, name, lat=34.1):
    return coptersonde_file(
        tmp_path, _epoch(*start), [0.0, 30.0, 60.0, 90.0],
        tdry_k=[293.15, 292.15, 291.15, 9.96921e36], td_c=[15.0, 14.0, 13.0, 0.0],
        pres_pa=[100000.0, 99000.0, 98000.0, 9.96921e36], wspd=[5.0, 6.0, 7.0, 0.0],
        wdir=[180.0, 270.0, 0.0, 0.0], alt_m=[100.0, 190.0, 280.0, 9.96921e36],
        lat=[lat] * 3 + [9.96921e36], lon=[-90.2] * 3 + [9.96921e36], name=name)


def test_coptersonde_converts_units_and_drops_trailing_fill(tmp_path):
    snd = copter.parse_coptersonde_netcdf(_ascent(tmp_path, (2023, 3, 3, 2, 12), "a.cdf"), "Site1")
    assert list(snd.temperature) == pytest.approx([20.0, 19.0, 18.0])
    assert list(snd.pressure) == pytest.approx([1000.0, 990.0, 980.0])
    assert list(snd.height) == pytest.approx([100.0, 190.0, 280.0])
    # from the south, from the west, from the north
    assert snd.u_wind == pytest.approx([0.0, 6.0, 0.0], abs=1e-9)
    assert snd.v_wind == pytest.approx([5.0, 0.0, -7.0], abs=1e-9)
    assert snd.valid_time == datetime(2023, 3, 3, 2, 12, tzinfo=UTC)


def test_coptersonde_all_fill_is_none(tmp_path):
    fill = 9.96921e36
    data = coptersonde_file(tmp_path, _epoch(2023, 3, 3), [0.0], [fill], [0.0], [fill], [0.0], [0.0],
                            [fill], [fill], [fill])
    assert copter.parse_coptersonde_netcdf(data, "Site1") is None


def test_coptersonde_groups_by_site_up_to_the_clock(monkeypatch, tmp_path):
    fake = FakeThredds(monkeypatch)
    folder = paths.coptersonde_iop("2023", "IOP1")
    fake.add(folder, "LakeVillage5N939.c1.20230303.021238.cdf", _ascent(tmp_path, (2023, 3, 3, 2, 12, 38), "1.cdf"))
    fake.add(folder, "LakeVillage5N939.c1.20230303.033714.cdf", _ascent(tmp_path, (2023, 3, 3, 3, 37, 14), "2.cdf"))
    fake.add(folder, "Schlater5N963.c1.20230303.023000.cdf", _ascent(tmp_path, (2023, 3, 3, 2, 30), "3.cdf", lat=33.6))
    fake.add(folder, "Schlater5N963.c1.20230303.050000.cdf", b"after the clock: never downloaded")
    fake.add(folder, "Schlater5N963.c1.20230304.010000.cdf", b"another day")
    fake.add(folder, "notes.txt", b"not an ascent")

    sets = copter.fetch_coptersonde_soundings(datetime(2023, 3, 3, 4, tzinfo=UTC))
    assert sorted(sets) == ["LakeVillage5N939", "Schlater5N963"]
    lake = sets["LakeVillage5N939"]
    assert lake.source == "coptersonde" and lake.is_coptersonde
    assert [s.slot_offset for s in lake.soundings] == [0, 1]
    assert [s.valid_time.strftime("%H%M") for s in lake.soundings] == ["0212", "0337"]
    assert sets["Schlater5N963"].lat == pytest.approx(33.6)
    assert not any("050000" in u or "20230304" in u for u in fake.file_requests())
    # other years' IOP folders aren't even listed
    assert not any("/2022/" in u for u in fake.requests)


def test_coptersonde_outside_perils_years_asks_nothing(monkeypatch):
    fake = FakeThredds(monkeypatch)
    assert copter.fetch_coptersonde_soundings(datetime(2024, 4, 27, tzinfo=UTC)) == {}
    assert fake.requests == []
    assert copter.coptersonde_years() == {2022, 2023}


# -- the NSSL provider chain ------------------------------------------------------------------

def _set(when, lat=35.0, source="nssl"):
    a = np.array([900.0, 800.0, 700.0, 600.0, 500.0])
    snd = Sounding(lat, -97.0, when, 0, "test", a, a, a, a, a, a, location_source="track")
    return SoundingSet(lat, -97.0, 300.0, when, [snd], source=source)


def _run_chain(fetcher, t):
    values, errors = [], []
    fetcher.sounding_ready.connect(values.append)
    fetcher.fetch_error.connect(errors.append)
    fetcher._do_fetch_nssl(t)
    return values, errors


def test_thredds_sondes_come_first_and_get_the_truck_lookup(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=UTC)
    expected = _set(t - timedelta(hours=1))
    seen = []
    monkeypatch.setattr(archive, "fetch_clamps_sonde_soundings",
                        lambda when, truck: seen.append(truck) or expected)

    def api_down():
        raise AssertionError("the API isn't asked when THREDDS has launches")

    monkeypatch.setattr(live, "_api_sonde_entries", api_down)
    fetcher = archive.ArchiveSoundingFetcher()
    lookup = lambda when: None
    fetcher.set_truck_position(lookup)
    values, errors = _run_chain(fetcher, t)
    assert values == [expected] and errors == [] and seen == [lookup]


def test_a_failing_provider_falls_through_and_the_future_never_shows(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=UTC)

    def down(*args):
        raise TimeoutError("down")

    monkeypatch.setattr(archive, "fetch_clamps_sonde_soundings", down)
    monkeypatch.setattr(live, "_api_sonde_entries", down)
    retrievals = _set(t + timedelta(hours=1), lat=36.0, source="clamps_tropoe")
    older = _set(t - timedelta(hours=1)).soundings[0]
    yesterday = _set(t - timedelta(days=1)).soundings[0]
    retrievals.soundings[:0] = [yesterday, older]
    monkeypatch.setattr(archive, "fetch_clamps_tropoe_soundings", lambda when: retrievals)
    values, errors = _run_chain(archive.ArchiveSoundingFetcher(), t)
    assert errors == []
    (sset,) = values
    assert sset.soundings == [older] and sset.source == "clamps_tropoe"
    assert sset.lat == 35.0 and sset.soundings[0].slot_offset == 0


def test_nothing_anywhere_says_why(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=UTC)
    monkeypatch.setattr(archive, "fetch_clamps_sonde_soundings", lambda when, truck: None)
    monkeypatch.setattr(live, "_api_sonde_entries", lambda: [])
    monkeypatch.setattr(archive, "fetch_clamps_tropoe_soundings", lambda when: None)
    values, errors = _run_chain(archive.ArchiveSoundingFetcher(), t)
    assert values == []
    assert errors == ["NSSL / CLAMPS sounding unavailable: none from THREDDS sonde, NSSL API, "
                      "CLAMPS TROPoe by 22:00Z on 2024-04-27"]


def test_provider_errors_are_listed(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=UTC)

    def down(*args):
        raise TimeoutError("down")

    monkeypatch.setattr(archive, "fetch_clamps_sonde_soundings", down)
    monkeypatch.setattr(live, "_api_sonde_entries", down)
    monkeypatch.setattr(archive, "fetch_clamps_tropoe_soundings", lambda when: None)
    _, errors = _run_chain(archive.ArchiveSoundingFetcher(), t)
    assert errors == ["NSSL / CLAMPS sounding unavailable: none from CLAMPS TROPoe by 22:00Z "
                      "on 2024-04-27; THREDDS sonde: down; NSSL API: down"]


def test_coptersondes_reach_the_signal(monkeypatch):
    t = datetime(2023, 3, 3, 4, tzinfo=UTC)
    sets = {"Site1": _set(t, source="coptersonde")}
    monkeypatch.setattr(archive, "fetch_coptersonde_soundings", lambda when: sets if when == t else {})
    fetcher = archive.ArchiveSoundingFetcher()
    got, errors = [], []
    fetcher.coptersondes_ready.connect(got.append)
    fetcher.fetch_error.connect(errors.append)
    fetcher._do_fetch_coptersondes(t)
    assert got == [sets] and errors == []

    def down(when):
        raise TimeoutError("down")

    monkeypatch.setattr(archive, "fetch_coptersonde_soundings", down)
    fetcher._do_fetch_coptersondes(t)
    assert errors == ["CopterSonde: down"]
