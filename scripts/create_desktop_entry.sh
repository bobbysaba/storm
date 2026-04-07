#!/bin/bash
# scripts/create_desktop_entry.sh — creates a STORM desktop launcher on Linux.
# Run from the project root:
#   bash scripts/create_desktop_entry.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="STORM"
ICON_SRC="$PROJECT_DIR/storm.png"
ICON_DEST="$HOME/.local/share/icons/storm.png"
DESKTOP_FILE="$HOME/.local/share/applications/storm.desktop"
DESKTOP_SHORTCUT="$HOME/Desktop/storm.desktop"

# ── Find conda base ────────────────────────────────────────────────────────────
CONDA_BASE=""
for DIR in \
    "$HOME/miniforge3" \
    "$HOME/opt/miniforge3" \
    "$HOME/miniforge" \
    "$HOME/miniconda3" \
    "$HOME/opt/miniconda3" \
    "$HOME/anaconda3" \
    "$HOME/opt/anaconda3" \
    "/opt/miniforge3" \
    "/opt/miniconda3" \
    "/opt/anaconda3" \
    "/opt/conda"; do
    if [ -d "$DIR" ]; then
        CONDA_BASE="$DIR"
        break
    fi
done

if [ -z "$CONDA_BASE" ]; then
    echo "ERROR: Could not find a conda installation."
    echo "       Install miniforge or miniconda and create the 'storm' environment first."
    exit 1
fi

# ── Find the storm environment python ─────────────────────────────────────────
PYTHON=""
ENV_PREFIX=""
for ENV_NAME in storm storm311; do
    CANDIDATE="$CONDA_BASE/envs/$ENV_NAME/bin/python"
    if [ -f "$CANDIDATE" ]; then
        PYTHON="$CANDIDATE"
        ENV_PREFIX="$CONDA_BASE/envs/$ENV_NAME"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: The 'storm' conda environment was not found."
    echo "       Create it with:  python setup.py"
    exit 1
fi

echo "Found python: $PYTHON"

# ── Write launcher script ──────────────────────────────────────────────────────
LAUNCHER="$PROJECT_DIR/scripts/launch_storm_linux.sh"

cat > "$LAUNCHER" << LAUNCHER_SCRIPT
#!/bin/bash
export PATH="$ENV_PREFIX/bin:$CONDA_BASE/bin:$CONDA_BASE/condabin:/usr/local/bin:/usr/bin:/bin"
export CONDA_PREFIX="$ENV_PREFIX"
cd "$PROJECT_DIR"
exec "$PYTHON" main.py
LAUNCHER_SCRIPT

chmod +x "$LAUNCHER"
echo "Launcher script: $LAUNCHER"

# ── Install icon ───────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$ICON_DEST")"
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$ICON_DEST"
    echo "Icon installed: $ICON_DEST"
else
    echo "Warning: storm.png not found — desktop entry will use a generic icon."
    ICON_DEST="utilities-terminal"
fi

# ── Write .desktop file ────────────────────────────────────────────────────────
mkdir -p "$(dirname "$DESKTOP_FILE")"

cat > "$DESKTOP_FILE" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=STORM
Comment=Launch the STORM application
Exec=$LAUNCHER
Icon=$ICON_DEST
Terminal=false
Categories=Science;
StartupNotify=true
DESKTOP

chmod +x "$DESKTOP_FILE"
echo "Application entry: $DESKTOP_FILE"

# ── Desktop shortcut ───────────────────────────────────────────────────────────
if [ -d "$HOME/Desktop" ]; then
    cp "$DESKTOP_FILE" "$DESKTOP_SHORTCUT"
    chmod +x "$DESKTOP_SHORTCUT"
    # GNOME requires marking the file as trusted
    if command -v gio &>/dev/null; then
        gio set "$DESKTOP_SHORTCUT" metadata::trusted true 2>/dev/null || true
    fi
    echo "Desktop shortcut: $DESKTOP_SHORTCUT"
else
    echo "No ~/Desktop directory found — skipping desktop shortcut."
fi

echo ""
echo "Done. STORM launcher installed."
echo "You may need to log out and back in (or run 'update-desktop-database') for"
echo "the app to appear in your application menu."
