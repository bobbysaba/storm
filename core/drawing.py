# core/drawing.py
# DrawingAnnotation dataclass for meteorological fronts and custom polylines/polygons.

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


FRONT_TYPES = [
    {"key": "cold_front",       "label": "Cold Front",       "color": "#4A9EFF", "symbol": "▼"},
    {"key": "warm_front",       "label": "Warm Front",       "color": "#E53935", "symbol": "●"},
    {"key": "stationary_front", "label": "Stationary Front", "color": "#4A9EFF", "symbol": "◆"},
    {"key": "occluded_front",   "label": "Occluded Front",   "color": "#9C27B0", "symbol": "◆"},
    {"key": "dryline",          "label": "Dryline",          "color": "#D4872E", "symbol": "~"},
]

CUSTOM_TYPES = [
    {"key": "polyline", "label": "Polyline", "color": "#E8EAF0", "symbol": "—"},
    {"key": "polygon",  "label": "Polygon",  "color": "#E8EAF0", "symbol": "□"},
]

ALL_DRAWING_TYPES = FRONT_TYPES + CUSTOM_TYPES
DRAWING_TYPE_MAP: dict[str, dict] = {t["key"]: t for t in ALL_DRAWING_TYPES}
FRONT_TYPE_KEYS: set[str] = {t["key"] for t in FRONT_TYPES}
CUSTOM_TYPE_KEYS: set[str] = {t["key"] for t in CUSTOM_TYPES}


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class DrawingAnnotation:
    id: str
    drawing_type: str       # one of the keys in ALL_DRAWING_TYPES
    coordinates: list       # [[lat, lon], ...]
    title: str              # shown on map for polyline/polygon; front type label otherwise
    creator: str
    created_at: datetime
    flipped: bool = False   # flip symbol side for fronts

    @classmethod
    def new(cls, drawing_type: str, coordinates: list,
            title: str = "", creator: str = "local",
            flipped: bool = False) -> "DrawingAnnotation":
        meta = DRAWING_TYPE_MAP.get(drawing_type, {})
        return cls(
            id=_short_uuid(),
            drawing_type=drawing_type,
            coordinates=coordinates,
            title=title or meta.get("label", drawing_type),
            creator=creator,
            created_at=datetime.now(timezone.utc),
            flipped=flipped,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "drawing_type": self.drawing_type,
            "coordinates": self.coordinates,
            "title": self.title,
            "creator": self.creator,
            "created_at": self.created_at.isoformat(),
            "flipped": self.flipped,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DrawingAnnotation":
        created_at = d.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        return cls(
            id=d["id"],
            drawing_type=d["drawing_type"],
            coordinates=d.get("coordinates", []),
            title=d.get("title", ""),
            creator=d.get("creator", "unknown"),
            created_at=created_at,
            flipped=d.get("flipped", False),
        )
