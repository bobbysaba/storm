
import os
import ssl
from pathlib import Path

# determine the parent directory for the application
_PROJ = Path(__file__).parent

# determine the path for the aws credentials
_AWS_CANDIDATES = (_PROJ / "aws", _PROJ / ".aws")

# get the files for the aws credentials
_AWS = next((p for p in _AWS_CANDIDATES if p.exists()), _AWS_CANDIDATES[0])

def _load_dotenv() -> None:
    """Read KEY=VALUE lines from an untracked ``.env`` in the project folder into
    the environment. Real environment variables win. Holds the API keys and the
    admin passphrase hash, which are kept out of the repository."""
    path = _PROJ / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# pull the application version
VERSION: str = (_PROJ / "VERSION").read_text().strip()

# set a default vehicle ID (will be overwritten)
VEHICLE_ID = "storm"

# set a default vehicle icon type (will be overwritten from launch dialog)
VEHICLE_ICON = "car"

# define the path to the obs file (will be overwritten)
OBS_FILE_DIR = ""
OBS_FILE_GPS_MODE = False   # True → GPS Ka column layout instead of FOFS

# define the interval to poll the obs file (local GUI update rate)
OBS_FILE_POLL_S = 1

# how often local obs are published to MQTT for other vehicles to see
OBS_MQTT_PUBLISH_S = 5

# column header names for real-time obs file
OBS_FILE_COL_LAT = "lat"
OBS_FILE_COL_LON = "lon"
OBS_FILE_COL_DATE = "gps_date"
OBS_FILE_COL_TIME = "gps_time"
OBS_FILE_COL_TEMP = "t_fast"
OBS_FILE_COL_DEWP = "dewpoint"
OBS_FILE_COL_WSPD = "sfc_wspd"
OBS_FILE_COL_WDIR = "sfc_wdir"
OBS_FILE_COL_PRES = "pressure"

# gps baud rate
GPS_BAUD = 4800

# path to previous deployment locations
DEPLOY_LOCS_FILE = str(_PROJ / "locs" / "deployment_locations.csv")

# pbkdf2-HMAC-SHA256 hash (600 000 iterations) of the admin mode passphrase.
# Not in the repo: set STORM_ADMIN_PASSPHRASE_HASH (environment or .env). Empty
# means admin mode is not configured.
ADMIN_PASSPHRASE_HASH = os.environ.get("STORM_ADMIN_PASSPHRASE_HASH", "")

# accent color
ACCENT_COLOR = "#00CFFF"

# openRouteService API key (used by routing_fetcher.py)
# (not in the repo: set ORS_API_KEY in the environment or .env)
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")

# Shared NSSL API settings for authenticated data endpoints.
NSSL_API_ROOT = os.environ.get(
    "NSSL_API_ROOT",
    "https://api.nssl.noaa.gov",
).rstrip("/")
# Not in the repo: set NSSL_API_KEY in the environment or .env.
NSSL_API_KEY = os.environ.get("NSSL_API_KEY", "")

# api.nssl.noaa.gov serves its TLS chain without the Sectigo intermediate CA.
# macOS/curl paper over this via AIA fetching, but Python's ssl module does
# not, so every NSSL API caller should use this context, which trusts the
# bundled intermediate explicitly.
_NSSL_INTERMEDIATE_CERT = _PROJ / "certs" / "sectigo_public_server_authentication_ca_dv_r36.pem"


def _build_nssl_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if _NSSL_INTERMEDIATE_CERT.is_file():
        try:
            ctx.load_verify_locations(cafile=str(_NSSL_INTERMEDIATE_CERT))
        except ssl.SSLError:
            pass
    return ctx


NSSL_SSL_CONTEXT = _build_nssl_ssl_context()

# SFCOA mesoanalysis API base URL.
SFCOA_BASE_URL = os.environ.get(
    "SFCOA_BASE_URL",
    f"{NSSL_API_ROOT}/data/sfcoa",
)

# home location fallback
HOME_LAT, HOME_LON = 35.22, -97.44   # Norman, OK

# mqtt endpoint
MQTT_HOST = "a2sxsx29lc4ofu-ats.iot.us-east-1.amazonaws.com"

# mqtt port
MQTT_PORT = 8883

# mqtt use tls boolean
MQTT_USE_TLS = True

# paths to certificates
MQTT_CA_CERT = str(_AWS / "storm.pem")
MQTT_CERT_FILE = str(_AWS / "storm.pem.crt")
MQTT_KEY_FILE = str(_AWS / "storm-private.pem.key")


def missing_mqtt_cert_files() -> list[str]:
    """The MQTT certificate files that aren't on disk; vehicle, monitor and
    archive modes won't launch until this is empty."""
    return [f for f in (MQTT_CA_CERT, MQTT_CERT_FILE, MQTT_KEY_FILE) if not os.path.isfile(f)]

# read-only viewer certificate, shipped with STORM: its AWS IoT policy
# (certs/viewer/viewer-policy.json) only lets it subscribe, and only with a
# "viewer-" client ID, so anyone can view but only aws/ holders publish
VIEWER_MQTT_CA_CERT = str(_PROJ / "certs" / "AmazonRootCA1.pem")
VIEWER_MQTT_CERT_FILE = str(_PROJ / "certs" / "viewer" / "viewer.pem.crt")
VIEWER_MQTT_KEY_FILE = str(_PROJ / "certs" / "viewer" / "viewer-private.pem.key")
VIEWER_MQTT_CLIENT_PREFIX = "viewer-"


def viewer_cert_present() -> bool:
    return all(os.path.isfile(f) for f in (VIEWER_MQTT_CA_CERT, VIEWER_MQTT_CERT_FILE, VIEWER_MQTT_KEY_FILE))


def mqtt_cert_files(viewer: bool = False) -> tuple[str, str, str]:
    """(CA, certificate, key) for the MQTT connection. VIEWER uses the shipped
    read-only certificate when it's there, else the publisher one in aws/."""
    if viewer and viewer_cert_present():
        return VIEWER_MQTT_CA_CERT, VIEWER_MQTT_CERT_FILE, VIEWER_MQTT_KEY_FILE
    return MQTT_CA_CERT, MQTT_CERT_FILE, MQTT_KEY_FILE

# data-age thresholds per layer, in minutes: (warn, stale). Ages past "warn"
# turn the drawer time label amber; past "stale" the toolbar button gets an
# amber dot. None disables that level (mesoanalysis / SFCOA frames are picked
# by the user, so only their errors are tracked). See ui/layer_status.py.
LAYER_AGE_THRESHOLDS_MIN: dict[str, tuple[float | None, float | None]] = {
    "radar":        (8, 12),
    "satellite":    (10, 20),
    "hazards":      (6, 10),    # polled every 2 min; age = time since last fetch
    "surface":      (12, 20),   # polled every 5 min; age = time since last fetch
    "mesoanalysis": (None, None),
    "sfcoa":        (None, None),
}
