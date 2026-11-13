"""Storm-track CSV files (core/storm_track.py) and where they live (core/workspace.py)."""
from datetime import date, datetime, timezone

import pytest

from core.storm_track import COLUMNS, TrackPoint, read_track_csv, write_track_csv
from core.workspace import atomic_write_text, track_path, unused_path

T = lambda h, m, s=0: datetime(2024, 4, 27, h, m, s, tzinfo=timezone.utc)


def test_one_file_per_case_date():
    assert track_path(date(2024, 4, 27)).name == "storm_20240427_track.csv"
    assert track_path(date(2024, 4, 27)).parent.name == "storm_tracks"


def test_a_written_track_reads_back_the_same(tmp_path):
    points = [
        TrackPoint(2, T(20, 4), 35.123456, -97.1, source="moved_retimed"),
        TrackPoint(1, T(20, 0), 35.0, -97.0, radar_site="KTLX", product="reflectivity",
                   product_label="Reflectivity (REF)", tilt_deg=0.5),
    ]
    path = tmp_path / "nested" / "track.csv"
    write_track_csv(points, path)
    assert path.read_text().splitlines()[0] == ",".join(COLUMNS)
    assert read_track_csv(path) == sorted(points, key=lambda p: p.time)


def test_mesoview_files_and_aliases_read_and_other_columns_are_ignored(tmp_path):
    path = tmp_path / "T10_20190528_2200_autosave_edited.csv"
    path.write_text(
        "Point_ID, DateTime ,Latitude,Longitude,case_id\n"
        "5,2019-05-28T22:03:00Z,35,-98,T10\n"
        "5,2019-05-28 22:01:00,35.1,-97.9,T10\n"          # repeated id: gets a fresh one
        ",2019-05-28 22:05:00,,-97.8,T10\n"               # no latitude: skipped
        ",not a time,35.2,-97.7,T10\n")
    points = read_track_csv(path)
    assert [p.time for p in points] == [datetime(2019, 5, 28, 22, 1, tzinfo=timezone.utc),
                                        datetime(2019, 5, 28, 22, 3, tzinfo=timezone.utc)]
    assert [p.point_id for p in points] == [5, 6]
    assert {p.source for p in points} == {"original"} and points[0].tilt_deg is None
    out = tmp_path / "out.csv"
    write_track_csv(points, out)
    assert "case_id" not in out.read_text()


def test_a_file_without_time_or_position_is_refused(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("when,lat\n2024-04-27 20:00,35\n")
    with pytest.raises(ValueError, match="time, lon"):
        read_track_csv(path)


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "track.csv"
    atomic_write_text(path, "old\n")
    monkeypatch.setattr("os.replace", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        atomic_write_text(path, "new\n")
    assert path.read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["track.csv"]     # no temp file left


def test_unused_path_numbers_existing_names(tmp_path):
    path = tmp_path / "t.csv"
    assert unused_path(path) == path
    path.write_text("")
    (tmp_path / "t_2.csv").write_text("")
    assert unused_path(path) == tmp_path / "t_3.csv"
