# Bobby Saba - define annotation dataclass

# import required packages
import uuid
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone

# define the annotation types and their labels, symbols, and colors
ANNOTATION_TYPES = [
    {"key": "road_closure",   "label": "Road Closure",       "symbol": "\u2715", "color": "#E53935"},
    {"key": "construction",   "label": "Construction",        "symbol": "\u25B2", "color": "#FFD166"},
    {"key": "flooded",        "label": "Flooded Road",        "symbol": "~",      "color": "#4A9EFF"},
    {"key": "downed_lines",   "label": "Downed Power Lines",  "symbol": "\u26A1", "color": "#FFD166"},
    {"key": "debris",         "label": "Road Debris",         "symbol": "!",      "color": "#FF6B35"},
]

# quick lookup by key
ANNOTATION_TYPE_MAP: dict[str, dict] = {t["key"]: t for t in ANNOTATION_TYPES}

# function to generate a short uuid
def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]

# define the annotation dataclass
@dataclass
class Annotation:
    id: str
    type_key: str
    label: str
    lat: float
    lon: float
    creator: str
    created_at: datetime
    ttl_hours: Optional[float] = None

    # method to create a new annotation
    @classmethod
    def new(cls, type_key: str, lat: float, lon: float,
            label: str = "", creator: str = "local",
            ttl_hours: Optional[float] = None) -> "Annotation":
        meta = ANNOTATION_TYPE_MAP.get(type_key, {})
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
    def to_dict(self) -> dict:
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
    def from_dict(cls, d: dict) -> "Annotation":
        created_at = d.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
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
