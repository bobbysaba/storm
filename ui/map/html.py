
import os
from pathlib import Path

import settings
from config import ACCENT_COLOR
from data import endpoints


# where the map opens: [map] in storm.toml
DEFAULT_LAT  = settings.SETTINGS.map.home_lat
DEFAULT_LON  = settings.SETTINGS.map.home_lon
DEFAULT_ZOOM = settings.SETTINGS.map.zoom
_STORM_BASE  = "storm://app"   # base for all asset/tile URLs

TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm.mbtiles")
)
_TILES_DIR = os.path.dirname(TILES_PATH)

# the area the shipped optional tile files cover, for files without bounds
_DEFAULT_BOUNDS = (-116.0, 28.0, -82.0, 49.0)


def _tile_bounds(name: str) -> str:
    """[west, south, east, north] from tiles/<name>'s metadata, as JS."""
    from core import mbtiles
    b = mbtiles.bounds(os.path.join(_TILES_DIR, name)) or _DEFAULT_BOUNDS
    return "[" + ", ".join(f"{v:g}" for v in b) + "]"

STATIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "static")
)

_TEMPLATE_PATH = Path(__file__).with_name("map_template.html")

# Without tiles/storm.mbtiles the base map comes over the network from
# OpenFreeMap: the same OpenMapTiles schema (layers, classes, name fields)
# the local file uses, so the style is unchanged. Free, no key; its TileJSON
# carries the OpenStreetMap/OpenMapTiles attribution the map shows.
ONLINE_TILEJSON = endpoints.OPENFREEMAP_TILEJSON


def basemap_source() -> str:
    """'local' when tiles/storm.mbtiles is there, otherwise 'online'."""
    from core import mbtiles
    return "local" if mbtiles.usable(TILES_PATH) else "online"


def build_map_html() -> str:
    """Build the full HTML page for the MapLibre map."""
    if basemap_source() == "local":
        tile_source = f'tiles: ["{_STORM_BASE}/tiles/{{z}}/{{x}}/{{y}}.pbf"],'
    else:
        tile_source = f'url: "{ONLINE_TILEJSON}",'
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace('tiles: ["__TILE_URL__"],', tile_source)
        .replace("__STORM_BASE__", _STORM_BASE)
        .replace("__ACCENT_COLOR__", ACCENT_COLOR)
        .replace("__DEFAULT_LON__", str(DEFAULT_LON))
        .replace("__DEFAULT_LAT__", str(DEFAULT_LAT))
        .replace("__DEFAULT_ZOOM__", str(DEFAULT_ZOOM))
        .replace("__UNITS_MI__", "true" if settings.SETTINGS.units.distance == "mi" else "false")
        .replace("__NLCD_BOUNDS__", _tile_bounds("storm_nlcd.mbtiles"))
        .replace("__SAT_BOUNDS__", _tile_bounds("satellite.mbtiles"))
    )
