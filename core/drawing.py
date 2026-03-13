# bobby saba - script that handles custom drawn annotations

# import required packages
import uuid
from datetime import datetime, timezone

# define the front types, their labels, symbols, and colors
FRONT_TYPES = [
    {"key": "cold_front", "label": "Cold Front", "color": "#4A9EFF", "symbol": "▼"},
    {"key": "warm_front", "label": "Warm Front", "color": "#E53935", "symbol": "●"},
    {"key": "stationary_front", "label": "Stationary Front", "color": "#4A9EFF", "symbol": "◆"},
    {"key": "occluded_front", "label": "Occluded Front", "color": "#9C27B0", "symbol": "◆"},
    {"key": "dryline", "label": "Dryline", "color": "#D4872E", "symbol": "~"},
]

# define the custom types, their labels, symbols, and colors
CUSTOM_TYPES = [
    {"key": "polyline", "label": "Polyline", "color": "#E8EAF0", "symbol": "—"},
    {"key": "polygon", "label": "Polygon", "color": "#E8EAF0", "symbol": "□"},
]

# combine the front and custom types to a master list
ALL_DRAWING_TYPES = FRONT_TYPES + CUSTOM_TYPES

# build lookup tables for drawing types
DRAWING_TYPE_MAP = {t["key"]: t for t in ALL_DRAWING_TYPES}
# set of front type keys
FRONT_TYPE_KEYS = {t["key"] for t in FRONT_TYPES}
# set of custom type keys
CUSTOM_TYPE_KEYS = {t["key"] for t in CUSTOM_TYPES}


# function to generate a short uuid
def _short_uuid():
    # return first 8 hex chars
    return uuid.uuid4().hex[:8]


# drawing annotation record
class DrawingAnnotation:
    # create a new drawing annotation
    def __init__(
        self,
        id,
        drawing_type,
        coordinates,
        title,
        creator,
        created_at,
        flipped=False,
    ):
        # assign id
        self.id = id
        # assign drawing type
        self.drawing_type = drawing_type
        # assign coordinates
        self.coordinates = coordinates
        # assign title
        self.title = title
        # assign creator
        self.creator = creator
        # assign created timestamp
        self.created_at = created_at
        # assign flipped flag
        self.flipped = flipped

    # build a new drawing annotation
    @classmethod
    def new(cls, drawing_type, coordinates, title="", creator="local", flipped=False):
        # get metadata for this type
        meta = DRAWING_TYPE_MAP.get(drawing_type, {})
        # return a new annotation
        return cls(
            id=_short_uuid(),
            drawing_type=drawing_type,
            coordinates=coordinates,
            title=title or meta.get("label", drawing_type),
            creator=creator,
            created_at=datetime.now(timezone.utc),
            flipped=flipped,
        )

    # convert annotation to dict
    def to_dict(self):
        # return json-friendly dict
        return {
            "id": self.id,
            "drawing_type": self.drawing_type,
            "coordinates": self.coordinates,
            "title": self.title,
            "creator": self.creator,
            "created_at": self.created_at.isoformat(),
            "flipped": self.flipped,
        }

    # build annotation from dict
    @classmethod
    def from_dict(cls, d):
        # get created timestamp
        created_at = d.get("created_at")
        # parse iso string if needed
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        # default to now
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        # return the annotation
        return cls(
            id=d["id"],
            drawing_type=d["drawing_type"],
            coordinates=d.get("coordinates", []),
            title=d.get("title", ""),
            creator=d.get("creator", "unknown"),
            created_at=created_at,
            flipped=d.get("flipped", False),
        )
