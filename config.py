
import os
from pathlib import Path

# per-install settings (storm.toml, .env) live in settings/; importing it
# first puts the .env keys in the environment for the lookups below
import settings

# determine the parent directory for the application
_PROJ = Path(__file__).parent

# pull the application version
VERSION: str = (_PROJ / "VERSION").read_text().strip()

# set a default vehicle ID (will be overwritten)
VEHICLE_ID = "storm"

# set a default vehicle icon type (will be overwritten from launch dialog)
VEHICLE_ICON = "car"

# define the path to the obs file (will be overwritten)
OBS_FILE_DIR = ""
OBS_FILE_LAYOUT = ""        # [obs_columns] layout name from the launch window; "" = the first

# define the interval to poll the obs file (local GUI update rate)
OBS_FILE_POLL_S = 1

# how often local obs are published to MQTT for other vehicles to see
OBS_MQTT_PUBLISH_S = 5

# the obs file's columns and name pattern are [obs_columns] in storm.toml (settings/)

# gps baud rate
GPS_BAUD = 4800

# pbkdf2-HMAC-SHA256 hash (600 000 iterations) of the admin mode passphrase.
# Not in the repo: set STORM_ADMIN_PASSPHRASE_HASH (environment or .env). Empty
# means admin mode is not configured.
ADMIN_PASSPHRASE_HASH = os.environ.get("STORM_ADMIN_PASSPHRASE_HASH", "")

# accent color
ACCENT_COLOR = "#00CFFF"

# openRouteService API key (used by routing_fetcher.py)
# (not in the repo: set ORS_API_KEY in the environment or .env)
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")

# home location fallback: [map] home_lat / home_lon in storm.toml
HOME_LAT, HOME_LON = settings.SETTINGS.map.home_lat, settings.SETTINGS.map.home_lon

# data-age thresholds per layer, in minutes: (warn, stale). Ages past "warn"
# turn the drawer time label amber; past "stale" the toolbar button gets an
# amber dot. None disables that level (Contour frames are picked by the user, so
# only its errors are tracked). See ui/layer_status.py.
LAYER_AGE_THRESHOLDS_MIN: dict[str, tuple[float | None, float | None]] = {
    "radar":        (8, 12),
    "satellite":    (10, 20),
    "hazards":      (6, 10),    # polled every 2 min; age = time since last fetch
    "surface":      (12, 20),   # polled every 5 min; age = time since last fetch
    "contours":        (None, None),
}
