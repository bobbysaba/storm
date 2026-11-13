"""core.storm_track: placing, moving and deleting points with undo / redo, and
what the map is given."""
from datetime import datetime, timedelta, timezone

from core.storm_track import StormTrack, TrackPoint

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)
RADAR = dict(radar_site="KTLX", product="reflectivity", product_label="Reflectivity (REF)",
             tilt_deg=0.5)


def _track(*minutes):
    track = StormTrack()
    for i, m in enumerate(minutes):
        track.place(T0 + timedelta(minutes=m), 35.0 + i / 10, -97.0 - i / 10, **RADAR)
    return track


def test_there_is_no_track_until_a_point_is_placed():
    track = StormTrack()
    assert len(track) == 0 and not track.can_undo() and track.nearest_id(T0) is None
    assert track.geojson(T0) == {"type": "FeatureCollection", "features": []}


def test_placed_points_keep_time_order_and_the_radar_on_screen():
    track = _track(4, 0, 2)
    assert [p.time.minute for p in track.points] == [0, 2, 4]
    assert [p.point_id for p in track.points] == [2, 3, 1]
    assert track.points[0].radar_site == "KTLX" and track.points[0].source == "manual"


def test_clicking_at_a_frame_that_has_a_point_moves_that_point():
    track = _track(0, 2)
    point, moved = track.place(T0 + timedelta(minutes=2, microseconds=300), 35.5, -97.5)
    assert moved and point.point_id == 2 and track.selected_id == 2
    assert len(track) == 2 and (track.points[1].lat, track.points[1].lon) == (35.5, -97.5)
    assert track.points[1].radar_site == "KTLX"      # keeps what it was placed on


def test_dragging_retimes_unless_the_time_is_kept():
    track = _track(0)
    track.move(1, 35.5, -97.5, None)                     # Alt held
    assert (track.points[0].lat, track.points[0].time) == (35.5, T0)
    assert track.points[0].source == "moved_position_only"
    later = T0 + timedelta(minutes=5)
    track.move(1, 35.6, -97.6, later)
    assert (track.points[0].lat, track.points[0].time) == (35.6, later)
    assert track.points[0].source == "moved_retimed"


def test_a_retimed_point_moves_to_its_place_in_time():
    track = _track(0, 2, 4)
    track.move(1, 35.0, -97.0, T0 + timedelta(minutes=3))
    assert [p.point_id for p in track.points] == [2, 1, 3]


def test_delete_undo_and_redo():
    track = _track(0, 2)
    track.selected_id = 1
    assert track.delete(1)
    assert [p.point_id for p in track.points] == [2] and track.selected_id is None
    assert track.undo() and [p.point_id for p in track.points] == [1, 2]
    assert track.redo() and [p.point_id for p in track.points] == [2]
    assert not track.redo()
    assert not track.delete(99)


def test_a_new_edit_drops_the_redo_steps():
    track = _track(0, 2)
    track.undo()
    track.place(T0 + timedelta(minutes=6), 35.3, -97.3)
    assert not track.can_redo()


def test_clear_is_one_undo_step():
    track = _track(0, 2, 4)
    track.clear()
    assert len(track) == 0
    track.undo()
    assert len(track) == 3


def test_new_ids_follow_the_highest_after_an_undo():
    track = _track(0, 2)
    track.undo()
    point, _ = track.place(T0 + timedelta(minutes=8), 35.3, -97.3)
    assert point.point_id == 2


def test_geojson_marks_the_selected_and_current_points_and_joins_them():
    track = _track(0, 2, 4)
    track.selected_id = 1
    fc = track.geojson(T0 + timedelta(minutes=3, seconds=50))
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    lines = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    assert [p["properties"]["state"] for p in points] == ["selected", "normal", "current"]
    assert len(lines) == 1 and len(lines[0]["geometry"]["coordinates"]) == 3
    assert points[0]["geometry"]["coordinates"] == [-97.0, 35.0]


def test_track_points_carry_their_time_for_the_hover_label():
    track = _track(0, 2)
    track.points.append(TrackPoint(9, T0 + timedelta(minutes=9), 35.0, -97.0))  # no radar recorded
    props = [f["properties"] for f in track.geojson(T0)["features"] if f["geometry"]["type"] == "Point"]
    assert [p["time"] for p in props][:2] == ["2024-04-27 20:00:00 UTC", "2024-04-27 20:02:00 UTC"]
    assert [p["order"] for p in props] == ["Point 1 of 3", "Point 2 of 3", "Point 3 of 3"]
    assert props[0]["radar"] == "KTLX Reflectivity (REF) 0.5°" and props[2]["radar"] == ""


def test_one_point_draws_no_line():
    fc = _track(0).geojson(T0)
    assert [f["geometry"]["type"] for f in fc["features"]] == ["Point"]
