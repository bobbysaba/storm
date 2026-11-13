"""Vehicle ID translation between STORM MQTT and FOFS THREDDS archives."""

from __future__ import annotations

# Platform folders under FOFS/Mobile-Mesonet/data on THREDDS, by their
# THREDDS names (thredds_vehicle_id passes them through unchanged). The
# index (archive/fofs_index.py) finds new folders by itself; this list
# names known vehicles inside odd folder names ("Probe_1"), and is the
# roster probed when no index is available. Not every platform has data
# every day; missing files are a fast 404.
KNOWN_FOFS_PLATFORMS = (
    "dltruck", "farfield", "hailcam", "mg1", "mg2", "mg3", "noxp_scout",
    "probe1", "probe2", "probe3", "probe4", "probe5", "probe7", "probe9",
    "windsonde1", "windsonde2",
)

_MQTT_TO_THREDDS = {
    "lid1": "dltruck",
    "p1": "probe1",
    "p2": "probe2",
    "p3": "probe3",
    "p4": "probe4",
    "p5": "probe5",
    "p7": "probe7",
}

_THREDDS_TO_MQTT = {
    thredds_id: mqtt_id
    for mqtt_id, thredds_id in _MQTT_TO_THREDDS.items()
}


def thredds_vehicle_id(mqtt_vehicle_id: str) -> str:
    """Return the FOFS directory name for a STORM MQTT vehicle ID."""
    normalized = mqtt_vehicle_id.strip().lower()
    return _MQTT_TO_THREDDS.get(normalized, normalized)


def mqtt_vehicle_id(thredds_vehicle_id: str) -> str:
    """Return the STORM MQTT ID for a FOFS directory name."""
    normalized = thredds_vehicle_id.strip().lower()
    return _THREDDS_TO_MQTT.get(normalized, normalized)

