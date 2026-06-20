
import math

from data.stations.sounding_stations import STATIONS

_EARTH_R_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return _EARTH_R_KM * 2 * math.asin(math.sqrt(a))


def nearest_obs_station(lat: float, lon: float) -> dict | None:
    """Return the nearest radiosonde station to (lat, lon), regardless of distance.

    Returns a dict with keys: station_id, name, lat, lon, elev, distance_km.
    Returns None only if STATIONS is empty.
    """
    best_id = None
    best_dist = float("inf")
    for sid, info in STATIONS.items():
        d = _haversine_km(lat, lon, info["lat"], info["lon"])
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


def nssl_within_radius_km(nssl_lat: float, nssl_lon: float,
                           target_lat: float, target_lon: float,
                           radius_km: float = 150.0) -> bool:
    """Return True if the NSSL CLAMPS launch point is within radius_km of target."""
    if nssl_lat == 0.0 and nssl_lon == 0.0:
        return False
    return _haversine_km(nssl_lat, nssl_lon, target_lat, target_lon) <= radius_km
