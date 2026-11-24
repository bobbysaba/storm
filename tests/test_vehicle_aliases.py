"""NSSL's vehicle IDs and FOFS folders: the same name from 3.0 on, and the
pre-3.0 broker IDs (lid1, p1-p7) still found under their folders."""

from archive.fetchers import mqtt_reader as mr
from providers.nssl.archive.vehicle_aliases import LEGACY_IDS, mqtt_vehicle_id, thredds_vehicle_id


def test_pre_3_0_ids_find_their_folders():
    assert thredds_vehicle_id("lid1") == "dltruck"
    for n in (1, 2, 3, 4, 5, 7):
        assert thredds_vehicle_id(f"p{n}") == f"probe{n}"


def test_folder_names_are_the_ids_now():
    for name in ("dltruck", "probe1", "hailcam"):
        assert thredds_vehicle_id(name) == name and mqtt_vehicle_id(name) == name


def test_matching_names_pass_through_normalized():
    assert thredds_vehicle_id(" HailCam ") == "hailcam"
    assert mqtt_vehicle_id("WindSonde1") == "windsonde1"


def test_recorded_traffic_renames_pre_3_0_ids(tmp_path):
    import json
    from datetime import datetime, timezone
    from archive import history
    rows = [{"vehicle_id": "LID1", "lat": 35.0, "lon": -97.0, "timestamp": "2024-04-27T21:00:00Z"},
            {"vehicle_id": "wx9", "lat": 35.0, "lon": -97.0, "timestamp": "2024-04-27T21:00:00Z"}]
    (tmp_path / "storm.vehicles.20240427").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    reader = mr.ArchiveMQTTReader(datetime(2024, 4, 27, 20, tzinfo=timezone.utc),
                                  sources=[history.FolderSource(tmp_path, "storm")], renames=LEGACY_IDS)
    reader._fetch_all()
    assert [obj["vehicle_id"] for _, obj in reader._data["vehicles"]] == ["dltruck", "wx9"]


def test_only_the_teams_provider_renames_its_traffic():
    import dataclasses
    import settings
    from settings import code_providers
    path = settings.PROJECT_DIR / "providers" / "nssl"
    guest = dataclasses.replace(settings.SETTINGS, code_providers=(settings.CodeProvider("nssl", path, builtin=True),))
    team = dataclasses.replace(settings.SETTINGS, code_providers=(settings.CodeProvider("nssl", path, (("team", True),)),))
    assert code_providers.team_vehicle_renames(guest) == {}
    assert code_providers.team_vehicle_renames(team)["lid1"] == "dltruck"
