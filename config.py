# config.py
# Application constants. VEHICLE_ID and OBS_FILE_DIR are defaults only —
# main.py overwrites them with values from the launch dialog before
# MainWindow is created.

from pathlib import Path

_PROJ = Path(__file__).parent
_AWS  = _PROJ / ".aws"

# ── Identity (overwritten by main.py after launch dialog) ─────────────────────

VEHICLE_ID: str = "STORM"

# ── Obs file watcher (Track A) ────────────────────────────────────────────────
# Directory containing YYYYMMDD.txt instrument logger files.
# Leave OBS_FILE_DIR empty to use GPS puck (Track B) instead.

OBS_FILE_DIR:    str = ""
OBS_FILE_POLL_S: int = 10

# Column name mapping — FOFS truck logger defaults (not user-configurable).
OBS_FILE_COL_LAT:       str = "lat"
OBS_FILE_COL_LON:       str = "lon"
OBS_FILE_COL_DATE:      str = "gps_date"
OBS_FILE_COL_TIME:      str = "gps_time"
OBS_FILE_COL_TIMESTAMP: str = ""
OBS_FILE_COL_TEMP:      str = "t_fast"
OBS_FILE_COL_DEWP:      str = "dewpoint"
OBS_FILE_COL_WSPD:      str = "sfc_wspd"
OBS_FILE_COL_WDIR:      str = "sfc_wdir"
OBS_FILE_COL_PRES:      str = "pressure"

# ── GPS (Track B) ─────────────────────────────────────────────────────────────

GPS_PORT: str = ""
GPS_BAUD: int = 4800

# ── Previous deployment locations ─────────────────────────────────────────────

DEPLOY_LOCS_FILE: str = str(_PROJ / "data" / "deployment_locations.json")

# ── UI ────────────────────────────────────────────────────────────────────────

ACCENT_COLOR: str = "#00CFFF"

# ── Vehicle JSON fetcher (hardcoded — same endpoint for all users) ────────────

VEHICLES_URL:    str = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/placefile_info/scout_locs.json"
VEHICLES_POLL_S: int = 10

# ── Home location fallback ────────────────────────────────────────────────────

HOME_LAT: float = 35.22   # Norman, OK
HOME_LON: float = -97.44

# ── MQTT (hardcoded — same broker and certs for all users) ───────────────────

MQTT_HOST:     str  = "a38pz70mp8mr8r-ats.iot.us-east-2.amazonaws.com"
MQTT_PORT:     int  = 8883
MQTT_USE_TLS:  bool = True
MQTT_CA_CERT:  str  = str(_AWS / "storm.pem")
MQTT_CERT_FILE: str = str(_AWS / "storm.pem.crt")
MQTT_KEY_FILE:  str = str(_AWS / "storm-private.pem.key")

