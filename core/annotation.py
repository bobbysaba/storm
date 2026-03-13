# bobby saba - define annotation data

# import required packages
import uuid
from datetime import datetime, timezone

# define the annotation types and their labels, symbols, and colors
ANNOTATION_TYPES = [
    {"key": "road_closure", "label": "Road Closure", "symbol": "\u2715", "color": "#E53935"},
    {"key": "construction", "label": "Construction", "symbol": "\u25B2", "color": "#FFD166"},
    {"key": "flooded", "label": "Flooded Road", "symbol": "~", "color": "#4A9EFF"},
    {"key": "downed_lines", "label": "Downed Power Lines", "symbol": "\u26A1", "color": "#FFD166"},
    {"key": "debris", "label": "Road Debris", "symbol": "!", "color": "#FF6B35"},
]

# quick lookup by key
ANNOTATION_TYPE_MAP = {t["key"]: t for t in ANNOTATION_TYPES}


# function to generate a short uuid
def _short_uuid():
    # return first 8 hex chars
    return uuid.uuid4().hex[:8]


# define the annotation object
class Annotation:
    # create a new annotation instance
    def __init__(
        self,
        id,
        type_key,
        label,
        lat,
        lon,
        creator,
        created_at,
        ttl_hours=None,
    ):
        # assign id
        self.id = id
        # assign type key
        self.type_key = type_key
        # assign label
        self.label = label
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign creator
        self.creator = creator
        # assign created timestamp
        self.created_at = created_at
        # assign optional ttl
        self.ttl_hours = ttl_hours

    # method to create a new annotation
    @classmethod
    def new(cls, type_key, lat, lon, label="", creator="local", ttl_hours=None):
        # get metadata for this type
        meta = ANNOTATION_TYPE_MAP.get(type_key, {})
        # return a new annotation
        return cls(
            id=_short_uuid(),
            type_key=type_key,
            label=label or meta.get("label", type_key),
            lat=lat,
            lon=lon,
            creator=creator,
            created_at=datetime.now(timezone.utc),
            ttl_hours=ttl_hours,
        )

    # method to convert annotation information to a dictionary
    def to_dict(self):
        # return json-friendly dict
        return {
            "id": self.id,
            "type_key": self.type_key,
            "label": self.label,
            "lat": self.lat,
            "lon": self.lon,
            "creator": self.creator,
            "created_at": self.created_at.isoformat(),
            "ttl_hours": self.ttl_hours,
        }

    # method to create an annotation from a dictionary
    @classmethod
    def from_dict(cls, d):
        # get the created timestamp
        created_at = d.get("created_at")
        # parse iso string if needed
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        # default to now
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        # build the annotation
        return cls(
            id=d["id"],
            type_key=d["type_key"],
            label=d.get("label", ""),
            lat=d["lat"],
            lon=d["lon"],
            creator=d.get("creator", "unknown"),
            created_at=created_at,
            ttl_hours=d.get("ttl_hours"),
        )
