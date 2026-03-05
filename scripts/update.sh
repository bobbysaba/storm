#!/bin/bash
# scripts/update.sh — pull the latest STORM code and update the conda environment.
# Run from the project root before a chase day:
#   bash scripts/update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STORM Updater"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Pull latest code ────────────────────────────────────────────────────────

if ! command -v git &>/dev/null; then
    echo "ERROR: git is not installed or not on PATH."
    exit 1
fi

echo ""
echo "Pulling latest code from GitHub..."
if ! git pull; then
    echo ""
    echo "ERROR: git pull failed."
    echo "This usually means you have local uncommitted changes conflicting with the update."
    echo "Check 'git status' and resolve any conflicts, then re-run this script."
    exit 1
fi

# ── 2. Update conda environment ────────────────────────────────────────────────

echo ""
echo "Updating conda environment..."

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

if [ -z "$CONDA_SH" ]; then
    echo "WARNING: conda not found — skipping environment update."
    echo "         Run 'bash setup_mac.sh' to set up the environment."
else
    # shellcheck disable=SC1090
    source "$CONDA_SH"
    conda env update -f "$PROJECT_DIR/envs/storm_mac.yml" --prune
    echo "conda environment up to date."
fi

# ── 3. Rebuild STORM.app (path is already baked in, but binary may be stale) ──

echo ""
echo "Rebuilding STORM.app..."
bash "$SCRIPT_DIR/create_app.sh"
echo "STORM.app rebuilt."

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Update complete! Launch STORM normally."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
