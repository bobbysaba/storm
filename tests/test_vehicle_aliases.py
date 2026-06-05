"""Tests for MQTT and FOFS vehicle ID translation."""

from archive.vehicle_aliases import mqtt_vehicle_id, thredds_vehicle_id


def test_known_mqtt_aliases_map_to_thredds_directories():
    assert thredds_vehicle_id("lid1") == "dltruck"
    assert thredds_vehicle_id("p1") == "probe1"
    assert thredds_vehicle_id("p2") == "probe2"


def test_probe_aliases_are_bidirectional():
    for probe_number in (1, 2, 3, 4, 5, 7):
        mqtt_id = f"p{probe_number}"
        thredds_id = f"probe{probe_number}"
        assert thredds_vehicle_id(mqtt_id) == thredds_id
        assert mqtt_vehicle_id(thredds_id) == mqtt_id


def test_matching_names_pass_through_normalized():
    assert thredds_vehicle_id(" HailCam ") == "hailcam"
    assert mqtt_vehicle_id("WindSonde1") == "windsonde1"
