"""
Wire format for vehicle observations on <topic_root>/vehicles/{vehicle_id}.

Every STORM client and the archive read this format, so any change here is a
protocol change (and needs docs/wire-format.md updated).
"""

from datetime import datetime, timezone

from . import topics
from .obs_tail import float_or_none
from .observation import Observation

# publish settings shared by the GUI and the headless publisher
PUBLISH_QOS = 1
PUBLISH_EXPIRY_S = 10800   # MQTT v5 message expiry: 3 hours

# Observation field -> payload keys a receiver accepts, first match wins.  The
# generic name comes first; the FOFS logger name (what STORM itself sends, and
# what past recordings hold) is the fallback.
MET_KEYS = {
    "temperature_c": ("temperature_c", "t_fast"),
    "dewpoint_c":    ("dewpoint_c", "dewpoint"),
    "wind_speed_ms": ("wind_speed_ms", "wspd"),
    "wind_dir_deg":  ("wind_dir_deg", "wdir"),
    "pressure_mb":   ("pressure_mb", "pressure"),
}


def vehicle_topic(vehicle_id: str, root: str = topics.DEFAULT_ROOT) -> str:
    return topics.topic(root, topics.VEHICLES, vehicle_id)


def build_payload(obs: Observation, default_icon: str | None = None) -> dict:
    """
    Build the outbound MQTT JSON payload.  Only includes met fields when
    present so GPS-only vehicles send a compact position-only message.
    """
    payload: dict = {
        "vehicle_id": obs.vehicle_id,
        "lat":        obs.lat,
        "lon":        obs.lon,
        "gps_date":   obs.timestamp.strftime("%d%m%y"),   # DDMMYY
        "gps_time":   obs.timestamp.strftime("%H%M%S"),   # HHMMSS
    }

    icon_type = obs.icon_type or default_icon
    if icon_type:
        payload["icon_type"] = icon_type
    if obs.wind_speed_ms  is not None: payload["wspd"]     = obs.wind_speed_ms
    if obs.wind_dir_deg   is not None: payload["wdir"]     = obs.wind_dir_deg
    if obs.temperature_c  is not None: payload["t_fast"]   = obs.temperature_c
    if obs.dewpoint_c     is not None: payload["dewpoint"] = obs.dewpoint_c
    if obs.pressure_mb    is not None: payload["pressure"] = obs.pressure_mb

    return payload


def observation_from_payload(payload: dict) -> Observation:
    """Read a vehicle payload: the generic names or the FOFS ones (MET_KEYS),
    and an ISO 8601 ``timestamp`` or ``gps_date`` + ``gps_time``."""
    met = {field: float_or_none(_first(payload, keys)) for field, keys in MET_KEYS.items()}
    return Observation(
        vehicle_id=payload["vehicle_id"],
        lat=float(payload["lat"]),
        lon=float(payload["lon"]),
        timestamp=_payload_timestamp(payload),
        icon_type=(payload.get("icon_type") or "").strip() or None,
        **met,
    )


def _first(payload: dict, keys: tuple[str, ...]):
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return None


def _payload_timestamp(payload: dict) -> datetime:
    """``timestamp`` (ISO 8601; no offset means UTC) when it parses, else
    gps_date + gps_time, else now."""
    raw = payload.get("timestamp")
    if isinstance(raw, str) and raw.strip():
        try:
            ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return parse_gps_timestamp(payload.get("gps_date"), payload.get("gps_time"))


def parse_gps_timestamp(gps_date: str | None, gps_time: str | None) -> datetime:
    """Parse DDMMYY + HHMMSS as UTC; fall back to now when missing or invalid."""
    date_s = (gps_date or "").strip()
    time_s = (gps_time or "").strip()
    if date_s and time_s:
        try:
            return datetime.strptime(date_s + time_s, "%d%m%y%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc)
