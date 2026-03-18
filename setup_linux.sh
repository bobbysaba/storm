#!/bin/bash
# setup_linux.sh — one-time setup for STORM on Linux.
# Run from the project root after cloning:
#   bash setup_linux.sh
#
# What it does:
#   1. Warns about required system libraries (Qt6/WebEngine deps)
#   2. Installs Miniforge (if conda is not already available)
#   3. Creates the 'storm' conda environment from storm_linux.yml
#   4. Creates a launcher script + optional .desktop shortcut

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 0. System library check ────────────────────────────────────────────────────
echo ""
echo "NOTE: Qt6 WebEngine requires system libraries not bundled by conda."
echo "On Debian/Ubuntu, install them with:"
echo "  sudo apt-get install -y libnss3 libxss1 libasound2 libatk-bridge2.0-0 \\"
echo "      libgtk-3-0 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2"
echo "On RHEL/Fedora:"
echo "  sudo dnf install -y nss libXScrnSaver alsa-lib atk gtk3"
echo ""
read -r -p "Press Enter to continue once dependencies are installed, or Ctrl+C to exit..."

# ── 1. Ensure conda is available ───────────────────────────────────────────────

CONDA_SH=""

if command -v conda &>/dev/null; then
    CONDA_BASE=$(conda info --base 2>/dev/null)
    CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
fi

if [ -z "$CONDA_SH" ]; then
    for DIR in \
        "$HOME/miniforge3" \
        "$HOME/opt/miniforge3" \
        "$HOME/miniforge" \
        "$HOME/miniconda3" \
        "$HOME/opt/miniconda3" \
        "$HOME/anaconda3" \
        "/opt/miniforge3" \
        "/opt/miniconda3" \
        "/opt/anaconda3"
    do
        if [ -f "$DIR/etc/profile.d/conda.sh" ]; then
            CONDA_SH="$DIR/etc/profile.d/conda.sh"
            break
        fi
    done
fi

if [ -z "$CONDA_SH" ]; then
    echo "Conda not found. Installing Miniforge..."
    ARCH=$(uname -m)
    INSTALLER="Miniforge3-Linux-${ARCH}.sh"
    INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/latest/download/$INSTALLER"
    INSTALLER_PATH="/tmp/$INSTALLER"

    curl -fsSL "$INSTALLER_URL" -o "$INSTALLER_PATH"
    bash "$INSTALLER_PATH" -b -p "$HOME/miniforge3"
    rm -f "$INSTALLER_PATH"

    CONDA_SH="$HOME/miniforge3/etc/profile.d/conda.sh"
    echo "Miniforge installed at $HOME/miniforge3"
fi

# shellcheck disable=SC1090
source "$CONDA_SH"

echo "Using conda at: $(conda info --base)"

# ── 2. Create the storm environment ───────────────────────────────────────────

if conda env list | grep -q "^storm "; then
    echo "storm environment already exists — skipping creation."
else
    echo "Creating storm conda environment (this may take a few minutes)..."
    conda env create -f "$SCRIPT_DIR/envs/storm_linux.yml"
    echo "storm environment created."
fi

# ── 3. Launcher script ─────────────────────────────────────────────────────────

LAUNCHER="$SCRIPT_DIR/storm_launcher.sh"
cat > "$LAUNCHER" << EOF
#!/bin/bash
source "\$(conda info --base)/etc/profile.d/conda.sh"
conda activate storm
cd "$SCRIPT_DIR"
python main.py "\$@"
EOF
chmod +x "$LAUNCHER"
echo "Launcher written to $LAUNCHER"

# ── 4. Optional .desktop shortcut ─────────────────────────────────────────────

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/storm.desktop"
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=STORM
Comment=Severe-weather Tactical Operations and Response Manager
Exec=$LAUNCHER
Icon=$SCRIPT_DIR/assets/icon.png
Terminal=false
Type=Application
Categories=Science;
EOF

echo ".desktop entry written to $DESKTOP_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete!"
echo "  Launch STORM with:"
echo "    bash $LAUNCHER"
echo "  Or find it in your application menu as 'STORM'."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
