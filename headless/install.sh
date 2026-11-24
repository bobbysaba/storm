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
#   --data-dir DIR      directory the logger writes its daily files into (required on first install)
#   --icon-type TYPE    car, drone, mesonet, lidar, radar, hailcam (default: car)
#   --host HOST         MQTT broker host (first install; can be set in the config later)
#   --port PORT         MQTT broker port (default: 8883, or 1883 with --tls none)
#   --tls MODE          none, server or mutual (default: server); mutual reads
#                       certs/storm.pem, certs/storm.pem.crt, certs/storm-private.pem.key
#   --topic-root ROOT   MQTT topic root (default: storm); must match the team's STORM setting
#   --profile NAME      take the broker from STORM's profiles/NAME.toml (e.g. nssl);
#                       --host / --port / --tls / --topic-root still win; certs go in certs/
#   --profile-file PATH the same from a settings file of your own (a private
#                       profile, or your team's storm.toml) instead of a shipped one;
#                       either profile's [obs_columns] is copied into the config
#   --layout NAME       which of the profile's [obs_columns.<name>] logger layouts
#                       this vehicle writes (default: the first)
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
MQTT_HOST=""
MQTT_PORT=""
MQTT_TLS=""
MQTT_TOPIC_ROOT=""
PROFILE=""
PROFILE_FILE=""
LAYOUT=""
P_OBS_COLUMNS=""
P_LAYOUTS=""
CA_NAME="storm.pem"
CERT_NAME="storm.pem.crt"
KEY_NAME="storm-private.pem.key"
INSTALL_SERVICE=1

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --vehicle-id) VEHICLE_ID="${2:?}"; shift 2 ;;
        --data-dir)   DATA_DIR="${2:?}"; shift 2 ;;
        --icon-type)  ICON_TYPE="${2:?}"; shift 2 ;;
        --host)       MQTT_HOST="${2:?}"; shift 2 ;;
        --port)       MQTT_PORT="${2:?}"; shift 2 ;;
        --tls)        MQTT_TLS="${2:?}"; shift 2 ;;
        --topic-root) MQTT_TOPIC_ROOT="${2:?}"; shift 2 ;;
        --profile)    PROFILE="${2:?}"; shift 2 ;;
        --layout)     LAYOUT="${2:?}"; shift 2 ;;
        --profile-file) PROFILE_FILE="${2:?}"; shift 2 ;;
        --prefix)     PREFIX="${2:?}"; shift 2 ;;
        --ref)        REF="${2:?}"; shift 2 ;;
        --local)      LOCAL="${2:?}"; shift 2 ;;
        --no-service) INSTALL_SERVICE=0; shift ;;
        -h|--help)    sed -n '2,26p' "$0" 2>/dev/null || true; exit 0 ;;
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
    [ -z "$PROFILE" ] || [ -z "$PROFILE_FILE" ] || die "give --profile or --profile-file, not both"
    if [ -n "$PROFILE_FILE" ]; then
        [ -f "$PROFILE_FILE" ] || die "no file $PROFILE_FILE"
        PROFILE_TEXT="$(cat "$PROFILE_FILE")" || die "couldn't read $PROFILE_FILE"
        case "$PROFILE_FILE" in *'"'*|*'\'*) die "quotes and backslashes are not allowed in options" ;; esac
        PROFILE="$(basename "$PROFILE_FILE")"
    elif [ -n "$PROFILE" ]; then
        case "$PROFILE" in *[!a-z0-9_-]*) die "--profile must be a plain name like nssl" ;; esac
        if [ -n "$LOCAL" ]; then
            PROFILE_TEXT="$(cat "$LOCAL/../profiles/$PROFILE.toml")" \
                || die "no profile $LOCAL/../profiles/$PROFILE.toml"
        else
            PROFILE_TEXT="$(curl -fsSL "https://raw.githubusercontent.com/bobbysaba/storm/$REF/profiles/$PROFILE.toml")" \
                || die "couldn't download profile '$PROFILE' (ref $REF)"
        fi
    fi
    if [ -n "$PROFILE" ]; then
        # the profile's [mqtt] table as shell variables; flags given on the
        # command line win, and certificate files keep their names in certs/
        eval "$(PROFILE_TEXT="$PROFILE_TEXT" python3 - <<'PY'
import json, os, shlex, tomllib
doc = tomllib.loads(os.environ["PROFILE_TEXT"])
m = doc.get("mqtt", {})
out = {"P_HOST": m.get("host", ""), "P_PORT": m.get("port", ""), "P_TLS": m.get("tls", ""),
       "P_TOPIC_ROOT": m.get("topic_root", "")}
for var, key in (("CA_NAME", "ca_cert"), ("CERT_NAME", "cert_file"), ("KEY_NAME", "key_file")):
    if m.get(key):
        out[var] = os.path.basename(str(m[key]))
# the [obs_columns] table (named layouts and units too), copied as is;
# storm-headless --check reports a bad one
def toml_table(name, table):
    lines = [f"[{name}]"] if any(not isinstance(v, dict) for v in table.values()) else []
    for k, v in table.items():
        if not isinstance(v, dict):
            lines.append(f"{k} = {json.dumps(str(v))}")
    for k, v in table.items():
        if isinstance(v, dict):
            lines += [""] + toml_table(f"{name}.{k}", v)
    return lines
cols = doc.get("obs_columns", {})
lines = toml_table("obs_columns", cols) if isinstance(cols, dict) and cols else []
if lines and not lines[0]:
    lines = lines[1:]
if isinstance(cols, dict):
    out["P_LAYOUTS"] = " ".join(k for k, v in cols.items() if isinstance(v, dict) and k != "units")
out["P_OBS_COLUMNS"] = "\n".join(lines)
for k, v in out.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
)" || die "profile '$PROFILE' is not valid TOML"
        MQTT_HOST="${MQTT_HOST:-$P_HOST}"
        MQTT_PORT="${MQTT_PORT:-$P_PORT}"
        MQTT_TLS="${MQTT_TLS:-$P_TLS}"
        MQTT_TOPIC_ROOT="${MQTT_TOPIC_ROOT:-$P_TOPIC_ROOT}"
    fi
    MQTT_TLS="${MQTT_TLS:-server}"
    if [ -n "$LAYOUT" ]; then
        case " $P_LAYOUTS " in *" $LAYOUT "*) ;; *) die "--layout must be one of the profile's [obs_columns] layouts: ${P_LAYOUTS:-none}" ;; esac
    fi
    case "$VEHICLE_ID$DATA_DIR$ICON_TYPE$MQTT_HOST$MQTT_TOPIC_ROOT$CA_NAME$CERT_NAME$KEY_NAME$LAYOUT" in *'"'*|*'\'*) die "quotes and backslashes are not allowed in options" ;; esac
    case "$MQTT_TLS" in none|server|mutual) ;; *) die "--tls must be none, server or mutual" ;; esac
    case "$MQTT_PORT" in ''|*[!0-9]*) [ -z "$MQTT_PORT" ] || die "--port must be a number" ;; esac
    case "$MQTT_TOPIC_ROOT" in *'+'*|*'#'*) die "--topic-root may not contain + or #" ;; esac
fi

# --- package -----------------------------------------------------------------
mkdir -p "$PREFIX/certs"
chmod 700 "$PREFIX/certs"

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
# STORM headless publisher config (written by install.sh${PROFILE:+ from profile $PROFILE}).
# Relative paths are resolved against this file's directory.

vehicle_id = "$VEHICLE_ID"
icon_type = "$ICON_TYPE"
data_dir = "$DATA_DIR"

# which [obs_columns.<name>] logger layout this vehicle writes (default: the first)
$( if [ -n "$LAYOUT" ]; then echo "layout = \"$LAYOUT\""; elif [ -n "$P_LAYOUTS" ]; then echo "# layout = \"${P_LAYOUTS%% *}\"     # one of: $P_LAYOUTS"; else echo '# layout = ""'; fi )

# seconds between file polls / between MQTT publishes
poll_s = 1
publish_s = 5

[mqtt]
# broker host; storm-headless --check fails until it is set
host = "$MQTT_HOST"
$( [ -n "$MQTT_PORT" ] && echo "port = $MQTT_PORT" || echo "# port = 8883" )
# none, server (TLS, broker verified) or mutual (TLS plus a client certificate)
tls = "$MQTT_TLS"
$( if [ "$MQTT_TLS" = mutual ]; then
    printf 'ca_cert = "certs/%s"\ncert_file = "certs/%s"\nkey_file = "certs/%s"' "$CA_NAME" "$CERT_NAME" "$KEY_NAME"
else
    printf '# ca_cert = "certs/ca.pem"     # empty: the system trust store'
fi )
# every topic sits under this; must match the team's STORM setting
$( [ -n "$MQTT_TOPIC_ROOT" ] && echo "topic_root = \"$MQTT_TOPIC_ROOT\"" || echo '# topic_root = "storm"' )
# login, if the broker uses one (the password can also come from STORM_MQTT_PASSWORD)
# username = ""
# password = ""
# defaults to "<vehicle_id>-pi"
# client_id = ""
# see docs/configuration.md in the STORM repository for every [mqtt] key
$( [ -n "$P_OBS_COLUMNS" ] && printf '\n# the logger columns from the profile\n%s' "$P_OBS_COLUMNS" )
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
        echo "    When the broker is set (and any certs are in $PREFIX/certs/) and the check passes:"
        echo "      $VENV/bin/storm-headless --config $CONFIG --check"
        echo "      sudo systemctl enable --now $SERVICE_NAME"
    fi
else
    info "skipped systemd; run manually with: $VENV/bin/storm-headless --config $CONFIG"
fi

echo
echo "Test the file parsing without MQTT any time with:"
echo "  $VENV/bin/storm-headless --config $CONFIG --dry-run"
