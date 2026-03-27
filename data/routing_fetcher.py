# data/routing_fetcher.py
# On-demand routing via OSRM public API + Nominatim geocoding.
#
# OSRM demo server: router.project-osrm.org — no API key, ~5–50 KB per request.
# Nominatim:        nominatim.openstreetmap.org — no API key, ~2–5 KB per lookup.
#                   OSM usage policy requires a User-Agent header.

import json
import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_OSRM_BASE       = "https://router.project-osrm.org/route/v1/driving"
_NOMINATIM_BASE  = "https://nominatim.openstreetmap.org/search"
_USER_AGENT      = "STORM-App/1.0 (storm-chasing field operations)"
_REQUEST_TIMEOUT = 15  # seconds

# Maps OSRM maneuver (type, modifier) → display arrow + short verb
_MANEUVER_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("depart",  ""):               ("↑", "Head"),
    ("arrive",  ""):               ("⦿", "Arrive at"),
    ("turn",    "left"):           ("←", "Turn left onto"),
    ("turn",    "right"):          ("→", "Turn right onto"),
    ("turn",    "slight left"):    ("↖", "Turn slight left onto"),
    ("turn",    "slight right"):   ("↗", "Turn slight right onto"),
    ("turn",    "sharp left"):     ("↙", "Turn sharp left onto"),
    ("turn",    "sharp right"):    ("↘", "Turn sharp right onto"),
    ("turn",    "straight"):       ("↑", "Continue straight on"),
    ("turn",    "uturn"):          ("↩", "Make a U-turn"),
    ("continue","straight"):       ("↑", "Continue on"),
    ("continue","left"):           ("←", "Keep left on"),
    ("continue","right"):          ("→", "Keep right on"),
    ("fork",    "left"):           ("↖", "Keep left at fork onto"),
    ("fork",    "right"):          ("↗", "Keep right at fork onto"),
    ("merge",   "left"):           ("↖", "Merge left onto"),
    ("merge",   "right"):          ("↗", "Merge right onto"),
    ("on ramp", "left"):           ("↖", "Take ramp on left onto"),
    ("on ramp", "right"):          ("↗", "Take ramp on right onto"),
    ("off ramp","left"):           ("↖", "Take exit left onto"),
    ("off ramp","right"):          ("↗", "Take exit right onto"),
    ("end of road","left"):        ("←", "Turn left at end of road onto"),
    ("end of road","right"):       ("→", "Turn right at end of road onto"),
    ("new name","straight"):       ("↑", "Continue onto"),
    ("roundabout",""):             ("↻", "Enter roundabout"),
    ("exit roundabout",""):        ("↑", "Exit roundabout onto"),
    ("rotary",""):                 ("↻", "Enter traffic circle"),
    ("exit rotary",""):            ("↑", "Exit traffic circle onto"),
}


@dataclass
class RouteStep:
    icon: str
    instruction: str
    distance_m: float
    duration_s: float
    location: tuple[float, float] = (0.0, 0.0)   # (lat, lon) of maneuver point


@dataclass
class RouteResult:
    geometry: dict          # GeoJSON LineString
    steps: list[RouteStep]
    distance_m: float
    duration_s: float
    origin_latlon: tuple[float, float]
    dest_latlon: tuple[float, float]


class RoutingFetcher(QObject):
    """Fire-and-forget fetcher for OSRM routes and Nominatim geocoding.

    All network I/O runs on daemon threads. Signals are emitted on the
    calling thread (connected to the Qt event loop via queued connections).

    Signals:
        route_ready(RouteResult)   — route successfully computed
        geocode_ready(str, float, float, bool)
                                   — (display_name, lat, lon, is_origin)
        fetch_error(str)           — any failure
    """

    route_ready   = pyqtSignal(object)            # RouteResult
    geocode_ready = pyqtSignal(str, float, float, bool)  # name, lat, lon, is_origin
    fetch_error   = pyqtSignal(str)

    def fetch_route(
        self,
        origin_lat: float, origin_lon: float,
        dest_lat: float,   dest_lon: float,
    ):
        """Start a background route fetch. Returns immediately."""
        threading.Thread(
            target=self._bg_route,
            args=(origin_lat, origin_lon, dest_lat, dest_lon),
            daemon=True,
        ).start()

    def geocode(self, address: str, is_origin: bool = False):
        """Start a background geocoding request. Returns immediately."""
        threading.Thread(
            target=self._bg_geocode,
            args=(address, is_origin),
            daemon=True,
        ).start()

    # ── Background workers ────────────────────────────────────────────────────

    def _bg_route(self, olat, olon, dlat, dlon):
        try:
            result = _fetch_route(olat, olon, dlat, dlon)
            self.route_ready.emit(result)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"Routing error: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"Routing failed: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)

    def _bg_geocode(self, address: str, is_origin: bool):
        try:
            name, lat, lon = _geocode_address(address)
            self.geocode_ready.emit(name, lat, lon, is_origin)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"Geocoding error: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"Geocoding failed: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)


# ── Network helpers ───────────────────────────────────────────────────────────

def _get_json(url: str) -> dict | list:
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    log.debug("fetch %s → %.1f KB", url[:80], len(raw) / 1024)
    return json.loads(raw)


def _geocode_address(address: str) -> tuple[str, float, float]:
    params = urlencode({"format": "json", "q": address, "limit": 1})
    url    = f"{_NOMINATIM_BASE}?{params}"
    data   = _get_json(url)
    if not data:
        raise ValueError(f"No results found for \"{address}\"")
    hit = data[0]
    return hit.get("display_name", address), float(hit["lat"]), float(hit["lon"])


def _pick_ref_badge(ref: str) -> str:
    """Return the most significant route reference as a [badge], or ''.

    OSRM often gives semicolon-separated designations, e.g. "I-44;US-270".
    Priority: Interstate > US Highway > everything else (state/county/local).
    """
    if not ref:
        return ""
    parts = [p.strip() for p in ref.split(";") if p.strip()]
    if not parts:
        return ""
    up = [p.upper() for p in parts]
    for i, u in enumerate(up):
        if u.startswith("I-") or u.startswith("I "):
            return f"[{parts[i]}]"
    for i, u in enumerate(up):
        if u.startswith("US") or u.startswith("U.S."):
            return f"[{parts[i]}]"
    return f"[{parts[0]}]"


def _fetch_route(
    olat: float, olon: float, dlat: float, dlon: float
) -> RouteResult:
    url = (
        f"{_OSRM_BASE}/{olon},{olat};{dlon},{dlat}"
        f"?steps=true&geometries=geojson&overview=full"
    )
    data = _get_json(url)

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"OSRM returned: {data.get('code', 'unknown error')}")

    route = data["routes"][0]
    geometry   = route["geometry"]          # GeoJSON LineString
    distance_m = route.get("distance", 0.0)
    duration_s = route.get("duration", 0.0)

    steps: list[RouteStep] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            maneuver = step.get("maneuver", {})
            m_type   = maneuver.get("type", "")
            m_mod    = maneuver.get("modifier", "")
            name     = step.get("name", "")
            badge    = _pick_ref_badge(step.get("ref", ""))

            icon, verb = _MANEUVER_MAP.get(
                (m_type, m_mod),
                _MANEUVER_MAP.get((m_type, ""), ("↑", m_type.capitalize() or "Continue")),
            )
            # Combine badge + name: "[I-44] Oklahoma Turnpike", "[I-44]", or "Main St"
            road = " ".join(filter(None, [badge, name]))
            instruction = f"{verb} {road}".strip() if road else verb

            loc = maneuver.get("location", [0.0, 0.0])  # OSRM: [lon, lat]
            steps.append(RouteStep(
                icon        = icon,
                instruction = instruction,
                distance_m  = step.get("distance", 0.0),
                duration_s  = step.get("duration", 0.0),
                location    = (loc[1], loc[0]),           # store as (lat, lon)
            ))

    return RouteResult(
        geometry      = geometry,
        steps         = steps,
        distance_m    = distance_m,
        duration_s    = duration_s,
        origin_latlon = (olat, olon),
        dest_latlon   = (dlat, dlon),
    )


# ── Formatting helpers (used by RoutingControls) ──────────────────────────────

def fmt_distance(meters: float) -> str:
    """Return a human-readable distance string (miles)."""
    miles = meters / 1609.34
    if miles < 0.1:
        feet = meters * 3.28084
        return f"{feet:.0f} ft"
    if miles < 10:
        return f"{miles:.1f} mi"
    return f"{miles:.0f} mi"


def fmt_duration(seconds: float) -> str:
    """Return a human-readable duration string."""
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours   = minutes // 60
    mins    = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"
