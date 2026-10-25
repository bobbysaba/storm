"""
Wire format for vehicle observations on storm/vehicles/{vehicle_id}.

Every STORM client and the archive read this format, so any change here is a
protocol change.
"""

from datetime import datetime, timezone

from .observation import Observation

TOPIC_PREFIX = "storm/vehicles"

# publish settings shared by the GUI and the headless publisher
PUBLISH_QOS = 1
PUBLISH_EXPIRY_S = 10800   # MQTT v5 message expiry: 3 hours


def vehicle_topic(vehicle_id: str) -> str:
    return f"{TOPIC_PREFIX}/{vehicle_id}"


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
    return Observation(
        vehicle_id=payload["vehicle_id"],
        lat=float(payload["lat"]),
        lon=float(payload["lon"]),
        timestamp=parse_gps_timestamp(payload.get("gps_date"), payload.get("gps_time")),
        icon_type=(payload.get("icon_type") or "").strip() or None,
        wind_speed_ms=_float_or_none(payload.get("wspd")),
        wind_dir_deg=_float_or_none(payload.get("wdir")),
        temperature_c=_float_or_none(payload.get("t_fast")),
        dewpoint_c=_float_or_none(payload.get("dewpoint")),
        pressure_mb=_float_or_none(payload.get("pressure")),
    )


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


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
