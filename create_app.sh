#!/bin/bash
# create_app.sh — builds STORM.app in the project directory.
# Run once from the project root:
#   chmod +x create_app.sh && ./create_app.sh
#
# The resulting STORM.app can be double-clicked from Finder or dragged
# to the Dock.  It activates the "storm" conda environment automatically.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="STORM"
APP_BUNDLE="$SCRIPT_DIR/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"

echo "Building $APP_BUNDLE ..."

# ── Clean and create bundle structure ─────────────────────────────────────────
rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

# ── Launcher script ───────────────────────────────────────────────────────────
# Placed at STORM.app/Contents/MacOS/STORM (the macOS executable entry point).
# Searches common conda installation paths, activates the storm env, and
# launches main.py from the project directory.

cat > "$MACOS_DIR/$APP_NAME" << 'LAUNCHER'
#!/bin/bash

# Project root is three levels up from this script
# (STORM.app/Contents/MacOS/ → STORM.app/Contents/ → STORM.app/ → project/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# ── Find conda ────────────────────────────────────────────────────────────────
CONDA_SH=""
for DIR in \
    "$HOME/miniforge3" \
    "$HOME/opt/miniforge3" \
    "$HOME/miniforge" \
    "$HOME/miniconda3" \
    "$HOME/opt/miniconda3" \
    "$HOME/anaconda3" \
    "$HOME/opt/anaconda3" \
    "/opt/homebrew/Caskroom/miniforge/base" \
    "/opt/miniconda3" \
    "/opt/anaconda3"; do
    if [ -f "$DIR/etc/profile.d/conda.sh" ]; then
        CONDA_SH="$DIR/etc/profile.d/conda.sh"
        break
    fi
done

if [ -z "$CONDA_SH" ]; then
    osascript -e 'display alert "STORM — Launch Error" message "Could not find a conda installation.\n\nPlease install miniforge or miniconda and create the \"storm\" environment." as critical'
    exit 1
fi

source "$CONDA_SH"

# Try "storm" first, fall back to "storm311" for older installs
conda activate storm 2>/dev/null || conda activate storm311 2>/dev/null

if [[ "$CONDA_DEFAULT_ENV" != "storm" && "$CONDA_DEFAULT_ENV" != "storm311" ]]; then
    osascript -e 'display alert "STORM — Launch Error" message "The \"storm\" conda environment was not found.\n\nCreate it with:\n  conda env create -f storm_mac.yml" as critical'
    exit 1
fi

cd "$PROJECT_DIR"
exec python main.py
LAUNCHER

chmod +x "$MACOS_DIR/$APP_NAME"

# ── Info.plist ────────────────────────────────────────────────────────────────
cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>STORM</string>
    <key>CFBundleDisplayName</key>
    <string>STORM</string>
    <key>CFBundleIdentifier</key>
    <string>edu.ou.nssl.storm</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>STORM</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>storm</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

# ── Icon (optional) ───────────────────────────────────────────────────────────
# If storm.icns exists in the project root, copy it into the bundle.
if [ -f "$SCRIPT_DIR/storm.icns" ]; then
    cp "$SCRIPT_DIR/storm.icns" "$RESOURCES_DIR/storm.icns"
    echo "  Icon: storm.icns copied."
else
    echo "  Icon: no storm.icns found — app will use a default icon."
    echo "        To add one: save a 1024×1024 PNG as storm.png and run:"
    echo "          python create_icon.py"
fi

echo ""
echo "Done.  STORM.app created at:"
echo "  $APP_BUNDLE"
echo ""
echo "You can drag it to your Dock or Applications folder."
