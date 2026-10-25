"""
Settings for the headless publisher, loaded from a TOML file and optionally
overridden on the command line.  See storm-headless.example.toml.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# same broker the GUI uses (config.MQTT_HOST in the main app)
DEFAULT_MQTT_HOST = "a38pz70mp8mr8r-ats.iot.us-east-2.amazonaws.com"
DEFAULT_MQTT_PORT = 8883

# file names the GUI expects inside its aws/ directory
DEFAULT_CA_CERT   = "storm.pem"
DEFAULT_CERT_FILE = "storm.pem.crt"
DEFAULT_KEY_FILE  = "storm-private.pem.key"

KNOWN_ICON_TYPES = ("car", "drone", "mesonet", "lidar", "radar")


class SettingsError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Settings:
    vehicle_id: str
    data_dir: Path
    icon_type: str = "car"
    # gps ka logger layout instead of fofs
    gps_mode: bool = False
    # seconds between file polls and between mqtt publishes
    poll_s: float = 1.0
    publish_s: float = 5.0
    mqtt_host: str = DEFAULT_MQTT_HOST
    mqtt_port: int = DEFAULT_MQTT_PORT
    # empty → "<vehicle_id>-pi" so a laptop on the same vehicle id never collides
    client_id: str = ""
    ca_cert: Path = Path(DEFAULT_CA_CERT)
    cert_file: Path = Path(DEFAULT_CERT_FILE)
    key_file: Path = Path(DEFAULT_KEY_FILE)

    @property
    def effective_client_id(self) -> str:
        return self.client_id or f"{self.vehicle_id}-pi"


def normalize_vehicle_id(raw: str) -> str:
    """Lowercase and replace anything outside [a-z0-9-] the same way the GUI does."""
    vid = (raw or "").strip().lower()
    return re.sub(r"[^a-z0-9-]+", "-", vid).strip("-")


def load_settings(config_path: str | Path | None, **overrides) -> Settings:
    """
    Read *config_path* (may be None when everything is given as overrides)
    and apply any non-None *overrides* (vehicle_id, data_dir, icon_type,
    gps_mode, client_id).  Relative paths resolve against the config file's
    directory.
    """
    raw: dict = {}
    base_dir = Path.cwd()
    if config_path is not None:
        path = Path(config_path).expanduser()
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError:
            raise SettingsError(f"config file not found: {path}") from None
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"invalid TOML in {path}: {e}") from None
        base_dir = path.resolve().parent

    for key, value in overrides.items():
        if value is not None:
            raw[key] = value

    mqtt = raw.get("mqtt", {})
    if not isinstance(mqtt, dict):
        raise SettingsError("[mqtt] must be a table")

    vehicle_id = normalize_vehicle_id(str(raw.get("vehicle_id", "")))
    if not vehicle_id or vehicle_id == "storm":
        raise SettingsError("vehicle_id is required (and may not be 'storm')")

    data_dir = raw.get("data_dir")
    if not data_dir:
        raise SettingsError("data_dir is required")

    icon_type = str(raw.get("icon_type", "car")).strip().lower() or "car"
    if icon_type not in KNOWN_ICON_TYPES:
        raise SettingsError(
            f"icon_type {icon_type!r} is not one of {', '.join(KNOWN_ICON_TYPES)}"
        )

    poll_s    = _positive_float(raw.get("poll_s", 1.0), "poll_s")
    publish_s = _positive_float(raw.get("publish_s", 5.0), "publish_s")

    cert_dir = _resolve(mqtt.get("cert_dir", "aws"), base_dir)

    return Settings(
        vehicle_id=vehicle_id,
        data_dir=_resolve(data_dir, base_dir),
        icon_type=icon_type,
        gps_mode=bool(raw.get("gps_mode", False)),
        poll_s=poll_s,
        publish_s=publish_s,
        mqtt_host=str(mqtt.get("host", DEFAULT_MQTT_HOST)),
        mqtt_port=int(mqtt.get("port", DEFAULT_MQTT_PORT)),
        client_id=str(raw.get("client_id") or mqtt.get("client_id") or "").strip(),
        ca_cert=_resolve(mqtt.get("ca_cert", DEFAULT_CA_CERT), cert_dir),
        cert_file=_resolve(mqtt.get("cert_file", DEFAULT_CERT_FILE), cert_dir),
        key_file=_resolve(mqtt.get("key_file", DEFAULT_KEY_FILE), cert_dir),
    )


def _resolve(value, base_dir: Path) -> Path:
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else base_dir / p


def _positive_float(value, name: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a number") from None
    if f <= 0:
        raise SettingsError(f"{name} must be greater than 0")
    return f
