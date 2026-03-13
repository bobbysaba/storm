# core/observation.py
# one meteorological observation record from a vehicle.

# import required packages
from datetime import datetime, timezone


# observation record
class Observation:
    # create a new observation instance
    def __init__(
        self,
        vehicle_id,
        lat,
        lon,
        timestamp,
        temperature_c=None,
        dewpoint_c=None,
        wind_speed_ms=None,
        wind_dir_deg=None,
        pressure_mb=None,
    ):
        # assign vehicle id
        self.vehicle_id = vehicle_id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign timestamp (utc)
        self.timestamp = timestamp
        # assign temperature (c)
        self.temperature_c = temperature_c
        # assign dewpoint (c)
        self.dewpoint_c = dewpoint_c
        # assign wind speed (m/s)
        self.wind_speed_ms = wind_speed_ms
        # assign wind direction (deg, from)
        self.wind_dir_deg = wind_dir_deg
        # assign pressure (mb/hpa)
        self.pressure_mb = pressure_mb

    # factory: create an observation timestamped to now (utc)
    @classmethod
    def new(cls, vehicle_id, lat, lon, **kwargs):
        # build a new record
        return cls(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.now(timezone.utc),
            **kwargs,
        )

    # convert to a json-serializable dict
    def to_dict(self):
        # return a json-friendly dict
        return {
            "vehicle_id": self.vehicle_id,
            "lat": self.lat,
            "lon": self.lon,
            "timestamp": self.timestamp.isoformat(),
            "temperature_c": self.temperature_c,
            "dewpoint_c": self.dewpoint_c,
            "wind_speed_ms": self.wind_speed_ms,
            "wind_dir_deg": self.wind_dir_deg,
            "pressure_mb": self.pressure_mb,
        }

    # build an observation from a dict
    @classmethod
    def from_dict(cls, d):
        # pull the timestamp (if present)
        ts = d.get("timestamp")
        # if timestamp is an iso string, parse it
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        # if no timestamp, use now
        elif ts is None:
            ts = datetime.now(timezone.utc)
        # return the record
        return cls(
            vehicle_id=d["vehicle_id"],
            lat=d["lat"],
            lon=d["lon"],
            timestamp=ts,
            temperature_c=d.get("temperature_c"),
            dewpoint_c=d.get("dewpoint_c"),
            wind_speed_ms=d.get("wind_speed_ms"),
            wind_dir_deg=d.get("wind_dir_deg"),
            pressure_mb=d.get("pressure_mb"),
        )
