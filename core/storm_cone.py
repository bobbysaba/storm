# core/storm_cone.py
# storm cone geometry helpers and geojson builder.

# import required packages
import math
import uuid
from datetime import datetime, timezone, timedelta

# earth radius in nautical miles
R_NM = 3440.065
# fixed angular half-width of the cone
_SPREAD_DEG = 20
# time steps for the cone boundary (hours)
_TIME_STEPS = [0, 0.25, 0.50, 0.75, 1.0]
# time steps for ribs (hours)
_RIB_STEPS = [0.25, 0.50, 0.75]
# points in the end-cap arc (inclusive)
_ARC_POINTS = 7


# function to generate a short uuid
def _short_uuid():
    # return first 8 hex chars
    return uuid.uuid4().hex[:8]


# spherical forward projection (returns lat, lon)
def _project(lat, lon, azimuth_deg, dist_nm):
    # convert to radians
    lat_r = math.radians(lat)
    # convert to radians
    lon_r = math.radians(lon)
    # convert to radians
    az_r = math.radians(azimuth_deg)
    # angular distance
    d_r = dist_nm / R_NM

    # compute destination latitude
    lat2 = math.asin(
        math.sin(lat_r) * math.cos(d_r)
        + math.cos(lat_r) * math.sin(d_r) * math.cos(az_r)
    )
    # compute destination longitude
    lon2 = lon_r + math.atan2(
        math.sin(az_r) * math.sin(d_r) * math.cos(lat_r),
        math.cos(d_r) - math.sin(lat_r) * math.sin(lat2),
    )
    # return degrees
    return math.degrees(lat2), math.degrees(lon2)


# storm cone record
class StormCone:
    # create a new storm cone instance
    def __init__(self, id, lat, lon, heading, speed_kts, creator, created_at):
        # assign id
        self.id = id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign heading (from)
        self.heading = heading
        # assign speed in knots
        self.speed_kts = speed_kts
        # assign creator
        self.creator = creator
        # assign created time
        self.created_at = created_at

    # factory to create a new storm cone
    @classmethod
    def new(cls, lat, lon, heading, speed_kts, creator="local"):
        # return a new record
        return cls(
            id=_short_uuid(),
            lat=lat,
            lon=lon,
            heading=heading,
            speed_kts=speed_kts,
            creator=creator,
            created_at=datetime.now(timezone.utc),
        )

    # distance (nm) along heading at time t (hours)
    def _dist(self, t_hours):
        # compute distance
        return self.speed_kts * t_hours

    # build a geojson feature collection
    def build_geojson(self):
        # pull the origin
        lat0, lon0 = self.lat, self.lon
        # storm travels opposite of the direction it comes from
        travel_az = (self.heading + 180) % 360

        # precompute right/left edge points at each time step
        right_pts = []
        left_pts = []
        for t in _TIME_STEPS:
            # compute distance at this time
            d = self._dist(t)
            # if zero distance, use origin
            if d == 0:
                right_pts.append((lon0, lat0))
                left_pts.append((lon0, lat0))
            else:
                # project right edge
                r_lat, r_lon = _project(lat0, lon0, travel_az + _SPREAD_DEG, d)
                # project left edge
                l_lat, l_lon = _project(lat0, lon0, travel_az - _SPREAD_DEG, d)
                # append points in geojson order
                right_pts.append((r_lon, r_lat))
                left_pts.append((l_lon, l_lat))

        # end-cap arc at t=1.0
        d_max = self._dist(1.0)
        # arc points list
        arc = []
        for i in range(_ARC_POINTS):
            # fraction across arc
            frac = i / (_ARC_POINTS - 1)
            # sweep from right edge to left edge
            az = (travel_az + _SPREAD_DEG) - frac * 2 * _SPREAD_DEG
            # handle zero distance
            if d_max == 0:
                arc.append((lon0, lat0))
            else:
                # project arc point
                a_lat, a_lon = _project(lat0, lon0, az, d_max)
                arc.append((a_lon, a_lat))

        # polygon ring: origin -> right edge -> arc -> left edge -> close
        ring = [(lon0, lat0)]
        ring.extend(right_pts[1:])
        ring.extend(arc[1:-1])
        ring.extend(reversed(left_pts[1:]))
        ring.append((lon0, lat0))

        # build the cone feature
        cone_feature = {
            "type": "Feature",
            "properties": {"ft": "cone", "cone_id": self.id},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }

        # rib features at 15/30/45 min
        rib_features = []
        # label features
        label_features = []
        for t in _RIB_STEPS:
            # compute distance
            d = self._dist(t)
            # compute minutes
            minutes = int(round(t * 60))
            # skip if no distance
            if d == 0:
                continue
            # project rib endpoints
            r_lat, r_lon = _project(lat0, lon0, travel_az + _SPREAD_DEG, d)
            l_lat, l_lon = _project(lat0, lon0, travel_az - _SPREAD_DEG, d)
            # append rib feature
            rib_features.append(
                {
                    "type": "Feature",
                    "properties": {"ft": "rib", "cone_id": self.id, "minutes": minutes},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[l_lon, l_lat], [r_lon, r_lat]],
                    },
                }
            )
            # label at centerline midpoint of rib
            c_lat, c_lon = _project(lat0, lon0, travel_az, d)
            # compute label time
            label_time = (self.created_at + timedelta(minutes=minutes)).strftime("%H%MZ")
            # append label feature
            label_features.append(
                {
                    "type": "Feature",
                    "properties": {"ft": "label", "cone_id": self.id, "text": label_time},
                    "geometry": {"type": "Point", "coordinates": [c_lon, c_lat]},
                }
            )

        # 60 min label at arc midpoint (centerline tip)
        if d_max > 0:
            # project tip
            tip_lat, tip_lon = _project(lat0, lon0, travel_az, d_max)
            # compute label time
            tip_time = (self.created_at + timedelta(minutes=60)).strftime("%H%MZ")
            # append label feature
            label_features.append(
                {
                    "type": "Feature",
                    "properties": {"ft": "label", "cone_id": self.id, "text": tip_time},
                    "geometry": {"type": "Point", "coordinates": [tip_lon, tip_lat]},
                }
            )

        # return the feature collection
        return {
            "type": "FeatureCollection",
            "features": [cone_feature] + rib_features + label_features,
        }

    # serialize to dict
    def to_dict(self):
        # return json-friendly dict
        return {
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "heading": self.heading,
            "speed_kts": self.speed_kts,
            "creator": self.creator,
            "created_at": self.created_at.isoformat(),
        }

    # build from dict
    @classmethod
    def from_dict(cls, d):
        # get created time
        created_at = d.get("created_at")
        # parse iso timestamp if present
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        # default to now
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        # return the record
        return cls(
            id=d["id"],
            lat=d["lat"],
            lon=d["lon"],
            heading=d["heading"],
            speed_kts=d["speed_kts"],
            creator=d.get("creator", "unknown"),
            created_at=created_at,
        )
