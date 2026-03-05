#!/bin/bash
# setup_mac.sh — one-time setup for STORM on macOS.
# Run from the project root after cloning:
#   bash setup_mac.sh
#
# What it does:
#   1. Installs Miniforge (if conda is not already available)
#   2. Creates the 'storm' conda environment from storm_mac.yml
#   3. Builds STORM.app and places a shortcut on the Desktop

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Ensure conda is available ───────────────────────────────────────────────

CONDA_SH=""

# Check if conda is already on PATH
if command -v conda &>/dev/null; then
    CONDA_BASE=$(conda info --base 2>/dev/null)
    CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
fi

# If not found, look in common install locations (mirrors launcher search paths)
if [ -z "$CONDA_SH" ]; then
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
        "/opt/anaconda3"
    do
        if [ -f "$DIR/etc/profile.d/conda.sh" ]; then
            CONDA_SH="$DIR/etc/profile.d/conda.sh"
            break
        fi
    done
fi

# Still not found — download and install Miniforge
if [ -z "$CONDA_SH" ]; then
    echo "Conda not found. Installing Miniforge..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        INSTALLER="Miniforge3-MacOSX-arm64.sh"
    else
        INSTALLER="Miniforge3-MacOSX-x86_64.sh"
    fi
    INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/latest/download/$INSTALLER"
    INSTALLER_PATH="/tmp/$INSTALLER"

    curl -fsSL "$INSTALLER_URL" -o "$INSTALLER_PATH"
    bash "$INSTALLER_PATH" -b -p "$HOME/miniforge3"
    rm -f "$INSTALLER_PATH"

    CONDA_SH="$HOME/miniforge3/etc/profile.d/conda.sh"
    echo "Miniforge installed at $HOME/miniforge3"
fi

# Source conda into this shell session
# shellcheck disable=SC1090
source "$CONDA_SH"

echo "Using conda at: $(conda info --base)"

# ── 2. Create the storm environment ───────────────────────────────────────────

if conda env list | grep -q "^storm "; then
    echo "storm environment already exists — skipping creation."
else
    echo "Creating storm conda environment (this may take a few minutes)..."
    conda env create -f "$SCRIPT_DIR/envs/storm_mac.yml"
    echo "storm environment created."
fi

# ── 3. Build STORM.app + Desktop shortcut ─────────────────────────────────────

echo "Building STORM.app..."
bash "$SCRIPT_DIR/scripts/create_app.sh"

DESKTOP="$HOME/Desktop"
if [ -d "$DESKTOP" ]; then
    # Remove any stale alias/symlink first
    rm -f "$DESKTOP/STORM.app"
    ln -s "$SCRIPT_DIR/STORM.app" "$DESKTOP/STORM.app"
    echo "STORM shortcut placed on Desktop."
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete!"
echo "  Launch STORM from your Desktop or run:"
echo "    conda activate storm && python main.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
