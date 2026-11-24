"""Vehicle IDs between NSSL's broker traffic and its FOFS THREDDS folders.

NSSL's vehicles publish under their folder names (dltruck, probe1, ...).
Before 3.0 they used other IDs, so recordings from then say lid1 and p1-p7:
LEGACY_IDS maps those, both for finding a vehicle's FOFS files and for
renaming the vehicles in NSSL's recorded traffic (VEHICLE_RENAMES in
providers/nssl/__init__.py). [archive] vehicle_aliases still applies on top,
for a setup that needs its own."""

from __future__ import annotations

import settings

# Platform folders under FOFS/Mobile-Mesonet/data on THREDDS, by their
# THREDDS names. The published index (providers/nssl/indexes.py FOFS)
# lists new vehicles by itself; this list names the calendar's registered
# vehicles, and is the roster probed when no index is available. Not every
# platform has data every day; missing files are a fast 404.
KNOWN_FOFS_PLATFORMS = (
    "dltruck", "farfield", "hailcam", "mg1", "mg2", "mg3", "noxp_scout",
    "probe1", "probe2", "probe3", "probe4", "probe5", "probe7", "probe9",
    "windsonde1", "windsonde2",
)


# pre-3.0 broker IDs -> the FOFS folder names the vehicles publish under now
LEGACY_IDS = {"lid1": "dltruck", "p1": "probe1", "p2": "probe2", "p3": "probe3",
              "p4": "probe4", "p5": "probe5", "p7": "probe7"}


def thredds_vehicle_id(mqtt_vehicle_id: str) -> str:
    """Return the FOFS directory name for a STORM MQTT vehicle ID."""
    vehicle = mqtt_vehicle_id.strip().lower()
    aliased = settings.SETTINGS.archive.file_vehicle(vehicle)
    return LEGACY_IDS.get(aliased, aliased)


def mqtt_vehicle_id(thredds_vehicle_id: str) -> str:
    """Return the STORM MQTT ID for a FOFS directory name."""
    return settings.SETTINGS.archive.broker_vehicle(thredds_vehicle_id.strip().lower())
