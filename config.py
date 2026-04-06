# Bobby Saba - config file to store application constants

# import required packages
from pathlib import Path

# determine the parent directory for the application
_PROJ = Path(__file__).parent

# determine the path for the aws credentials
_AWS_CANDIDATES = (_PROJ / "aws", _PROJ / ".aws")

# get the files for the aws credentials
_AWS = next((p for p in _AWS_CANDIDATES if p.exists()), _AWS_CANDIDATES[0])

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
OBS_MQTT_PUBLISH_S = 10

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

# GPS port (will be overwritten)
GPS_PORT = ""

# GPS baud rate
GPS_BAUD = 4800

# path to previous deployment locations
DEPLOY_LOCS_FILE = str(_PROJ / "locs" / "deployment_locations.csv")

# ── Mode passphrases ──────────────────────────────────────────────────────────
# SHA-256 hashes of the passphrases required to launch in vehicle or monitor
# mode.  The plaintext passwords are never stored here — only hashes.
#
# To generate a new hash:
#   python3 -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
#
# Replace the placeholder values below with hashes of your actual passwords.
VEHICLE_PASSPHRASE_HASH = "6f3924cf58c4302ac1d1743807806f5cac6af1dd163ceea88407dee66eaa046e"
MONITOR_PASSPHRASE_HASH = "3aa29dabcf48a07aae0fd782da7c48705f82614eaa0e0fcf02bbb42cb6db13d0"
ARCHIVE_PASSPHRASE_HASH = "ce8c1196ed2eeceb1d6ed967566c95be2e418b306e96f13ac3a791c4987f5e3b"

# accent color
ACCENT_COLOR = "#00CFFF"

# ── Archive mode ───────────────────────────────────────────────────────────────
# Base URL for the server hosting MQTT archive JSONL files.
# Files are expected at: {ARCHIVE_MQTT_BASE_URL}/{YYYY-MM-DD}/{topic}.jsonl
# Leave blank to disable MQTT replay in archive mode.
ARCHIVE_MQTT_BASE_URL = ""

# OpenRouteService API key (used by routing_fetcher.py)
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjlhZTYwOTkzMzcwMTRlYjg5YTcxMjlkYmU0MGI1NTRmIiwiaCI6Im11cm11cjY0In0="

# home location fallback
HOME_LAT, HOME_LON = 35.22, -97.44   # Norman, OK

# mqtt endpoint
MQTT_HOST = "a38pz70mp8mr8r-ats.iot.us-east-2.amazonaws.com"

# mqtt port
MQTT_PORT = 8883

# mqtt use tls boolean
MQTT_USE_TLS = True

# paths to certificates
MQTT_CA_CERT = str(_AWS / "storm.pem")
MQTT_CERT_FILE = str(_AWS / "storm.pem.crt")
MQTT_KEY_FILE = str(_AWS / "storm-private.pem.key")
