
import os
from pathlib import Path

from config import ACCENT_COLOR


DEFAULT_LAT  = 35.22
DEFAULT_LON  = -97.44
DEFAULT_ZOOM = 6
_STORM_BASE  = "storm://app"   # base for all asset/tile URLs

TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm.mbtiles")
)

STATIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "static")
)

_TEMPLATE_PATH = Path(__file__).with_name("map_template.html")


def build_map_html() -> str:
    """Build the full HTML page for the MapLibre map."""
    tile_url = f"{_STORM_BASE}/tiles/{{z}}/{{x}}/{{y}}.pbf"
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("__STORM_BASE__", _STORM_BASE)
        .replace("__TILE_URL__", tile_url)
        .replace("__ACCENT_COLOR__", ACCENT_COLOR)
        .replace("__DEFAULT_LON__", str(DEFAULT_LON))
        .replace("__DEFAULT_LAT__", str(DEFAULT_LAT))
        .replace("__DEFAULT_ZOOM__", str(DEFAULT_ZOOM))
    )
