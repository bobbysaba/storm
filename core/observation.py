# core/observation.py
# One meteorological observation record from a vehicle.

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Observation:
    vehicle_id:    str
    lat:           float
    lon:           float
    timestamp:     datetime
    temperature_c: float | None = None   # °C; displayed as °F
    dewpoint_c:    float | None = None   # °C; displayed as °F
    wind_speed_ms: float | None = None   # m/s; converted to knots for MetPy
    wind_dir_deg:  float | None = None   # meteorological degrees (from)
    pressure_mb:   float | None = None   # mb / hPa

    @classmethod
    def new(cls, vehicle_id: str, lat: float, lon: float, **kwargs) -> "Observation":
        """Factory: create an Observation timestamped to now (UTC)."""
        return cls(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.now(timezone.utc),
            **kwargs,
        )

    def to_dict(self) -> dict:
        return {
            "vehicle_id":    self.vehicle_id,
            "lat":           self.lat,
            "lon":           self.lon,
            "timestamp":     self.timestamp.isoformat(),
            "temperature_c": self.temperature_c,
            "dewpoint_c":    self.dewpoint_c,
            "wind_speed_ms": self.wind_speed_ms,
            "wind_dir_deg":  self.wind_dir_deg,
            "pressure_mb":   self.pressure_mb,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        ts = d.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        elif ts is None:
            ts = datetime.now(timezone.utc)
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
