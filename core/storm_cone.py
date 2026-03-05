# core/storm_cone.py
# StormCone dataclass + spherical projection math + GeoJSON builder.

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

R_NM = 3440.065  # Earth radius in nautical miles
_SPREAD_DEG = 20  # fixed angular half-width of the cone
_TIME_STEPS = [0, 0.25, 0.50, 0.75, 1.0]  # hours
_RIB_STEPS  = [0.25, 0.50, 0.75]          # hours (15 / 30 / 45 min ribs)
_ARC_POINTS = 7  # points in the end-cap arc (inclusive of both edges)


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


def _project(lat: float, lon: float, azimuth_deg: float, dist_nm: float) -> tuple[float, float]:
    """Spherical forward projection. Returns (lat, lon)."""
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    az_r  = math.radians(azimuth_deg)
    d_r   = dist_nm / R_NM

    lat2 = math.asin(
        math.sin(lat_r) * math.cos(d_r)
        + math.cos(lat_r) * math.sin(d_r) * math.cos(az_r)
    )
    lon2 = lon_r + math.atan2(
        math.sin(az_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


@dataclass
class StormCone:
    id: str
    lat: float
    lon: float
    heading: float    # degrees — direction storm is coming *from* (meteorological convention)
    speed_kts: float
    creator: str
    created_at: datetime

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def new(cls, lat: float, lon: float, heading: float, speed_kts: float,
            creator: str = "local") -> "StormCone":
        return cls(
            id=_short_uuid(),
            lat=lat,
            lon=lon,
            heading=heading,
            speed_kts=speed_kts,
            creator=creator,
            created_at=datetime.now(timezone.utc),
        )

    # ── Geometry ─────────────────────────────────────────────────────────────

    def _dist(self, t_hours: float) -> float:
        """Distance (nm) along heading at time t (hours)."""
        return self.speed_kts * t_hours

    def build_geojson(self) -> dict:
        """
        Build a GeoJSON FeatureCollection with:
          - one Polygon  feature (ft='cone')  — the full cone area
          - three LineString features (ft='rib')   — ribs at 15/30/45 min
          - four Point features (ft='label') — time labels at 15/30/45/60 min
        heading is meteorological: direction storm comes FROM.
        travel_az = (heading + 180) % 360.
        """
        lat0, lon0 = self.lat, self.lon
        # Storm travels in the opposite direction from which it comes
        travel_az = (self.heading + 180) % 360

        # precompute right/left edge points at each time step
        right_pts = []   # (lon, lat) — GeoJSON order
        left_pts  = []
        for t in _TIME_STEPS:
            d = self._dist(t)
            if d == 0:
                right_pts.append((lon0, lat0))
                left_pts.append((lon0, lat0))
            else:
                r_lat, r_lon = _project(lat0, lon0, travel_az + _SPREAD_DEG, d)
                l_lat, l_lon = _project(lat0, lon0, travel_az - _SPREAD_DEG, d)
                right_pts.append((r_lon, r_lat))
                left_pts.append((l_lon, l_lat))

        # end-cap arc at t=1.0 — sweeps travel_az+20 → travel_az-20 (clockwise)
        d_max = self._dist(1.0)
        arc = []
        for i in range(_ARC_POINTS):
            frac = i / (_ARC_POINTS - 1)
            az = (travel_az + _SPREAD_DEG) - frac * 2 * _SPREAD_DEG
            if d_max == 0:
                arc.append((lon0, lat0))
            else:
                a_lat, a_lon = _project(lat0, lon0, az, d_max)
                arc.append((a_lon, a_lat))

        # ── polygon ring: origin → right edge (t=0..1) → arc → left edge
        #    reversed (t=1..0) → close
        ring = [(lon0, lat0)]
        ring.extend(right_pts[1:])   # skip t=0 (already added as origin)
        ring.extend(arc[1:-1])       # arc interior (edges shared with right/left at t=1)
        ring.extend(reversed(left_pts[1:]))  # left edge, tip to origin
        ring.append((lon0, lat0))    # close

        cone_feature = {
            "type": "Feature",
            "properties": {"ft": "cone", "cone_id": self.id},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }

        # ── rib features at 15 / 30 / 45 min
        rib_features = []
        label_features = []
        for t in _RIB_STEPS:
            d = self._dist(t)
            minutes = int(round(t * 60))
            if d == 0:
                continue
            r_lat, r_lon = _project(lat0, lon0, travel_az + _SPREAD_DEG, d)
            l_lat, l_lon = _project(lat0, lon0, travel_az - _SPREAD_DEG, d)
            rib_features.append({
                "type": "Feature",
                "properties": {"ft": "rib", "cone_id": self.id, "minutes": minutes},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[l_lon, l_lat], [r_lon, r_lat]],
                },
            })
            # label at centerline midpoint of rib
            c_lat, c_lon = _project(lat0, lon0, travel_az, d)
            label_time = (self.created_at + timedelta(minutes=minutes)).strftime("%H%MZ")
            label_features.append({
                "type": "Feature",
                "properties": {"ft": "label", "cone_id": self.id, "text": label_time},
                "geometry": {"type": "Point", "coordinates": [c_lon, c_lat]},
            })

        # 60 min label at arc midpoint (centerline tip)
        if d_max > 0:
            tip_lat, tip_lon = _project(lat0, lon0, travel_az, d_max)
            tip_time = (self.created_at + timedelta(minutes=60)).strftime("%H%MZ")
            label_features.append({
                "type": "Feature",
                "properties": {"ft": "label", "cone_id": self.id, "text": tip_time},
                "geometry": {"type": "Point", "coordinates": [tip_lon, tip_lat]},
            })

        return {
            "type": "FeatureCollection",
            "features": [cone_feature] + rib_features + label_features,
        }

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "heading": self.heading,
            "speed_kts": self.speed_kts,
            "creator": self.creator,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StormCone":
        created_at = d.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        return cls(
            id=d["id"],
            lat=d["lat"],
            lon=d["lon"],
            heading=d["heading"],
            speed_kts=d["speed_kts"],
            creator=d.get("creator", "unknown"),
            created_at=created_at,
        )
