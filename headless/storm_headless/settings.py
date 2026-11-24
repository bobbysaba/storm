"""
Settings for the headless publisher, loaded from a TOML file and optionally
overridden on the command line.  See storm-headless.example.toml.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .mqtt_settings import MQTTSettings, MQTTSettingsError, parse_mqtt
from .obs_columns import FieldMap, ObsColumnsError, parse_obs_layouts, pick_layout

# the map icons STORM draws (ui/map/map_template.html, the launch window)
KNOWN_ICON_TYPES = ("car", "drone", "mesonet", "lidar", "radar", "hailcam")


class SettingsError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Settings:
    vehicle_id: str
    data_dir: Path
    icon_type: str = "car"
    # the logger's layouts, the same [obs_columns] table as storm.toml
    obs_layouts: tuple[tuple[str, FieldMap], ...] = (("default", FieldMap()),)
    # which of them this logger writes ("" = the first)
    layout: str = ""
    # seconds between file polls and between mqtt publishes
    poll_s: float = 1.0
    publish_s: float = 5.0
    # the [mqtt] table, same schema as the GUI's storm.toml
    mqtt: MQTTSettings = field(default_factory=MQTTSettings)
    # empty → "<client_id_prefix><vehicle_id>-pi" so a laptop on the same
    # vehicle id never collides
    client_id: str = ""

    @property
    def obs_columns(self) -> FieldMap:
        """The logger's layout."""
        return pick_layout(self.obs_layouts, self.layout)

    @property
    def layout_name(self) -> str:
        return self.layout or self.obs_layouts[0][0]

    @property
    def effective_client_id(self) -> str:
        return self.client_id or self.mqtt.client_id(f"{self.vehicle_id}-pi")


def normalize_vehicle_id(raw: str) -> str:
    """Lowercase and replace anything outside [a-z0-9-] the same way the GUI does."""
    vid = (raw or "").strip().lower()
    return re.sub(r"[^a-z0-9-]+", "-", vid).strip("-")


def load_settings(config_path: str | Path | None, **overrides) -> Settings:
    """
    Read *config_path* (may be None when everything is given as overrides)
    and apply any non-None *overrides* (vehicle_id, data_dir, icon_type,
    layout, client_id).  Relative paths resolve against the config file's
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
    # client_id is headless-only: the GUI's IDs come from the launch window
    mqtt_table = {k: v for k, v in mqtt.items() if k != "client_id"}
    try:
        broker = parse_mqtt(mqtt_table, base_dir)
    except MQTTSettingsError as e:
        raise SettingsError(str(e)) from None

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

    if "gps_mode" in raw:
        raise SettingsError("gps_mode is gone: name the logger's layout with layout = \"<name>\" "
                            "(an [obs_columns.<name>] table; see https://bobbysaba.com/docs/storm/ref/configuration/)")
    try:
        obs_layouts = parse_obs_layouts(raw.get("obs_columns", {}))
    except ObsColumnsError as e:
        raise SettingsError(str(e)) from None
    layout = raw.get("layout", "")
    names = [name for name, _ in obs_layouts]
    if not isinstance(layout, str) or (layout.strip() and layout.strip() not in names):
        raise SettingsError(f"layout must be one of the [obs_columns] layouts: {', '.join(names)}")

    poll_s    = _positive_float(raw.get("poll_s", 1.0), "poll_s")
    publish_s = _positive_float(raw.get("publish_s", 5.0), "publish_s")

    return Settings(
        vehicle_id=vehicle_id,
        data_dir=_resolve(data_dir, base_dir),
        icon_type=icon_type,
        obs_layouts=obs_layouts,
        layout=layout.strip(),
        poll_s=poll_s,
        publish_s=publish_s,
        mqtt=broker,
        client_id=str(raw.get("client_id") or mqtt.get("client_id") or "").strip(),
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
