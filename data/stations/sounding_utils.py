from core.geo import haversine_km
from data.stations.sounding_stations import STATIONS


def nearest_obs_station(lat: float, lon: float) -> dict | None:
    """Return the nearest radiosonde station to (lat, lon), regardless of distance.

    Returns a dict with keys: station_id, name, lat, lon, elev, distance_km.
    Returns None only if STATIONS is empty.
    """
    best_id = None
    best_dist = float("inf")
    for sid, info in STATIONS.items():
        d = haversine_km(lat, lon, info["lat"], info["lon"])
        if d < best_dist:
            best_dist = d
            best_id = sid

    if best_id is None:
        return None

    info = STATIONS[best_id]
    return {
        "station_id":  best_id,
        "name":        info["name"],
        "lat":         info["lat"],
        "lon":         info["lon"],
        "elev":        info["elev"],
        "distance_km": best_dist,
    }


def launch_within_radius_km(launch_lat: float, launch_lon: float,
                            target_lat: float, target_lon: float,
                            radius_km: float = 150.0) -> bool:
    """Return True if a sounding's launch point (a provider's or a source's)
    is within radius_km of target; 0, 0 is an unplaced launch."""
    if launch_lat == 0.0 and launch_lon == 0.0:
        return False
    return haversine_km(launch_lat, launch_lon, target_lat, target_lon) <= radius_km
