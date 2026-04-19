
import math
from datetime import datetime, timezone


R_M = 6371008.8
SCAN_MODES = {"point", "circle", "sector"}
DEFAULT_POINT_RADIUS_M = 60.0
DEFAULT_COLOR = "#00CFFF"


def _project(lat: float, lon: float, azimuth_deg: float, dist_m: float) -> tuple[float, float]:
    """Project a point along a true bearing by distance in meters."""
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    az_r = math.radians(azimuth_deg)
    d_r = dist_m / R_M

    lat2 = math.asin(
        math.sin(lat_r) * math.cos(d_r)
        + math.cos(lat_r) * math.sin(d_r) * math.cos(az_r)
    )
    lon2 = lon_r + math.atan2(
        math.sin(az_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _parse_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _closed_ring(points: list[list[float]]) -> list[list[float]]:
    if points and points[0] != points[-1]:
        points.append(list(points[0]))
    return points


def _arc_points(
    lat: float,
    lon: float,
    start_deg: float,
    sweep_deg: float,
    radius_m: float,
    min_points: int = 16,
) -> list[list[float]]:
    step_deg = 4.0
    count = max(min_points, int(abs(sweep_deg) / step_deg) + 1)
    pts = []
    for idx in range(count):
        frac = idx / (count - 1) if count > 1 else 0
        az = start_deg + sweep_deg * frac
        p_lat, p_lon = _project(lat, lon, az, radius_m)
        pts.append([p_lon, p_lat])
    return pts


class ScanSector:
    """Represents the current area where a vehicle is collecting data."""

    def __init__(
        self,
        vehicle_id: str,
        active: bool,
        mode: str,
        lat: float,
        lon: float,
        timestamp: datetime | None = None,
        range_m: float | None = None,
        inner_range_m: float = 0.0,
        azimuth_deg: float | None = None,
        beam_width_deg: float | None = None,
        follow_vehicle: bool | None = None,
        color: str = DEFAULT_COLOR,
        label: str = "",
    ):
        mode = (mode or "point").strip().lower()
        if mode not in SCAN_MODES:
            raise ValueError(f"unsupported scan mode: {mode}")

        self.vehicle_id = str(vehicle_id)
        self.active = bool(active)
        self.mode = mode
        self.lat = float(lat)
        self.lon = float(lon)
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.range_m = _float_or_none(range_m)
        self.inner_range_m = max(0.0, float(inner_range_m or 0.0))
        self.azimuth_deg = _float_or_none(azimuth_deg)
        self.beam_width_deg = _float_or_none(beam_width_deg)
        self.follow_vehicle = (mode == "point") if follow_vehicle is None else bool(follow_vehicle)
        self.color = color or DEFAULT_COLOR
        self.label = label or ""

    def to_dict(self) -> dict:
        payload = {
            "vehicle_id": self.vehicle_id,
            "active": self.active,
            "mode": self.mode,
            "lat": self.lat,
            "lon": self.lon,
            "timestamp": _iso(self.timestamp),
            "follow_vehicle": self.follow_vehicle,
        }
        if self.range_m is not None:
            payload["range_m"] = self.range_m
        if self.inner_range_m:
            payload["inner_range_m"] = self.inner_range_m
        if self.azimuth_deg is not None:
            payload["azimuth_deg"] = self.azimuth_deg
        if self.beam_width_deg is not None:
            payload["beam_width_deg"] = self.beam_width_deg
        if self.color:
            payload["color"] = self.color
        if self.label:
            payload["label"] = self.label
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "ScanSector":
        return cls(
            vehicle_id=data["vehicle_id"],
            active=bool(data.get("active", True)),
            mode=data.get("mode", "point"),
            lat=float(data.get("lat", 0.0)),
            lon=float(data.get("lon", 0.0)),
            timestamp=_parse_timestamp(data.get("timestamp")),
            range_m=data.get("range_m"),
            inner_range_m=float(data.get("inner_range_m") or 0.0),
            azimuth_deg=data.get("azimuth_deg"),
            beam_width_deg=data.get("beam_width_deg"),
            follow_vehicle=data.get("follow_vehicle"),
            color=data.get("color") or DEFAULT_COLOR,
            label=data.get("label") or "",
        )

    def build_geojson(self) -> dict:
        if not self.active:
            return {"type": "FeatureCollection", "features": []}
        feature = self._build_feature()
        return {"type": "FeatureCollection", "features": [feature] if feature else []}

    def _props(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "mode": self.mode,
            "active": self.active,
            "color": self.color,
            "label": self.label or self.vehicle_id,
            "timestamp": _iso(self.timestamp),
        }

    def _build_feature(self) -> dict | None:
        if self.mode == "point":
            return {
                "type": "Feature",
                "properties": self._props() | {"radius_m": DEFAULT_POINT_RADIUS_M},
                "geometry": {"type": "Point", "coordinates": [self.lon, self.lat]},
            }
        if self.mode == "circle":
            return self._build_circle()
        if self.mode == "sector":
            return self._build_sector()
        return None

    def _outer_range(self) -> float:
        return max(0.0, float(self.range_m or 0.0))

    def _build_circle(self) -> dict | None:
        outer_m = self._outer_range()
        if outer_m <= 0:
            return None
        outer = _closed_ring(_arc_points(self.lat, self.lon, 0.0, 360.0, outer_m, min_points=72))
        rings = [outer]
        if 0 < self.inner_range_m < outer_m:
            inner = _closed_ring(_arc_points(self.lat, self.lon, 360.0, -360.0, self.inner_range_m, min_points=72))
            rings.append(inner)
        return {
            "type": "Feature",
            "properties": self._props(),
            "geometry": {"type": "Polygon", "coordinates": rings},
        }

    def _build_sector(self) -> dict | None:
        outer_m = self._outer_range()
        if outer_m <= 0 or self.azimuth_deg is None or self.beam_width_deg is None:
            return None
        width = max(0.1, min(360.0, float(self.beam_width_deg)))
        start = float(self.azimuth_deg) - width / 2.0
        outer_arc = _arc_points(self.lat, self.lon, start, width, outer_m)

        if 0 < self.inner_range_m < outer_m:
            inner_arc = _arc_points(self.lat, self.lon, start + width, -width, self.inner_range_m)
            ring = _closed_ring(outer_arc + inner_arc)
        else:
            ring = _closed_ring([[self.lon, self.lat]] + outer_arc)

        return {
            "type": "Feature",
            "properties": self._props(),
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }


def feature_collection(scan_sectors: list[ScanSector]) -> dict:
    features = []
    for scan in scan_sectors:
        features.extend(scan.build_geojson()["features"])
    return {"type": "FeatureCollection", "features": features}


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
