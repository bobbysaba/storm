#!/usr/bin/env bash
# Install (or update) the STORM headless publisher on a Raspberry Pi or any
# Debian-style Linux box.  Safe to re-run: it upgrades the package and keeps
# an existing config.
#
#   curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh \
#     | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet
#
# Options:
#   --vehicle-id ID     vehicle id shown in STORM (required on first install)
#   --data-dir DIR      directory the logger writes YYYYMMDD.txt into (required on first install)
#   --icon-type TYPE    car, drone, mesonet, lidar, radar (default: car)
#   --prefix DIR        install location (default: ~/storm-headless)
#   --ref REF           git branch/tag to install (default: main)
#   --local PATH        install from a local checkout's headless/ folder instead of GitHub
#   --no-service        don't install the systemd service
set -euo pipefail

REPO_URL="https://github.com/bobbysaba/storm.git"
PREFIX="$HOME/storm-headless"
REF="main"
LOCAL=""
VEHICLE_ID=""
DATA_DIR=""
ICON_TYPE="car"
INSTALL_SERVICE=1

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --vehicle-id) VEHICLE_ID="${2:?}"; shift 2 ;;
        --data-dir)   DATA_DIR="${2:?}"; shift 2 ;;
        --icon-type)  ICON_TYPE="${2:?}"; shift 2 ;;
        --prefix)     PREFIX="${2:?}"; shift 2 ;;
        --ref)        REF="${2:?}"; shift 2 ;;
        --local)      LOCAL="${2:?}"; shift 2 ;;
        --no-service) INSTALL_SERVICE=0; shift ;;
        -h|--help)    sed -n '2,17p' "$0" 2>/dev/null || true; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

CONFIG="$PREFIX/storm-headless.toml"
VENV="$PREFIX/venv"
SERVICE_NAME="storm-headless"
SERVICE_FILE="$PREFIX/$SERVICE_NAME.service"
RUN_USER="$(id -un)"

# --- prerequisites -----------------------------------------------------------
command -v python3 >/dev/null || die "python3 not found (sudo apt install -y python3)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || die "python 3.11+ required, found $(python3 -V 2>&1)"
python3 -c 'import ensurepip, venv' 2>/dev/null \
    || die "python venv support missing (sudo apt install -y python3-venv)"
if [ -z "$LOCAL" ]; then
    command -v git >/dev/null || die "git not found (sudo apt install -y git)"
fi

if [ ! -f "$CONFIG" ]; then
    [ -n "$VEHICLE_ID" ] || die "--vehicle-id is required on first install"
    [ -n "$DATA_DIR" ]   || die "--data-dir is required on first install"
    case "$VEHICLE_ID$DATA_DIR$ICON_TYPE" in *'"'*|*'\'*) die "quotes and backslashes are not allowed in options" ;; esac
fi

# --- package -----------------------------------------------------------------
mkdir -p "$PREFIX/aws"
chmod 700 "$PREFIX/aws"

if [ ! -x "$VENV/bin/python" ]; then
    info "creating virtualenv in $VENV"
    python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip

if [ -n "$LOCAL" ]; then
    SRC="$(cd "$LOCAL" && pwd)"
    [ -f "$SRC/pyproject.toml" ] || die "$SRC is not the headless/ folder (no pyproject.toml)"
else
    SRC="git+$REPO_URL@$REF#subdirectory=headless"
fi
info "installing storm-headless from $SRC"
"$VENV/bin/python" -m pip install --quiet --upgrade --force-reinstall --no-deps "$SRC"
"$VENV/bin/python" -m pip install --quiet "$SRC"   # pulls paho-mqtt if missing
info "installed $("$VENV/bin/storm-headless" --version)"

# --- config ------------------------------------------------------------------
if [ -f "$CONFIG" ]; then
    info "keeping existing config $CONFIG"
else
    info "writing $CONFIG"
    cat > "$CONFIG" <<EOF
# STORM headless publisher config (written by install.sh).
# Relative paths are resolved against this file's directory.

vehicle_id = "$VEHICLE_ID"
icon_type = "$ICON_TYPE"
data_dir = "$DATA_DIR"

# true for the GPS Ka logger layout instead of FOFS
gps_mode = false

# seconds between file polls / between MQTT publishes
poll_s = 1
publish_s = 5

[mqtt]
host = "a38pz70mp8mr8r-ats.iot.us-east-2.amazonaws.com"
port = 8883
# defaults to "<vehicle_id>-pi"
# client_id = ""

# folder holding storm.pem, storm.pem.crt, storm-private.pem.key
cert_dir = "aws"
EOF
fi

# --- systemd -----------------------------------------------------------------
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=STORM headless vehicle publisher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PREFIX
ExecStart=$VENV/bin/storm-headless --config $CONFIG
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

certs_ready() {
    "$VENV/bin/storm-headless" --config "$CONFIG" --check >/dev/null 2>&1
}

echo
"$VENV/bin/storm-headless" --config "$CONFIG" --check || true
echo

if [ "$INSTALL_SERVICE" -eq 1 ] && command -v systemctl >/dev/null; then
    info "installing systemd unit /etc/systemd/system/$SERVICE_NAME.service"
    sudo cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME.service"
    sudo systemctl daemon-reload
    if certs_ready; then
        sudo systemctl enable "$SERVICE_NAME"
        sudo systemctl restart "$SERVICE_NAME"
        info "service running; follow logs with: journalctl -u $SERVICE_NAME -f"
    else
        # don't enable a service that would crash-loop until certs arrive
        info "service installed but NOT started (see the check above)."
        echo "    When the certs are in $PREFIX/aws/ and the check passes:"
        echo "      $VENV/bin/storm-headless --config $CONFIG --check"
        echo "      sudo systemctl enable --now $SERVICE_NAME"
    fi
else
    info "skipped systemd; run manually with: $VENV/bin/storm-headless --config $CONFIG"
fi

echo
echo "Test the file parsing without MQTT any time with:"
echo "  $VENV/bin/storm-headless --config $CONFIG --dry-run"
