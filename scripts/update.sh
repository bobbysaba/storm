#!/bin/bash
# scripts/update.sh — pull the latest STORM code.
# Run from the project root before a chase day:
#   bash scripts/update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STORM Updater"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if ! command -v git &>/dev/null; then
    echo "ERROR: git is not installed or not on PATH."
    exit 1
fi

echo "Pulling latest code from GitHub..."
if ! git pull; then
    echo ""
    echo "ERROR: git pull failed."
    echo "This usually means you have local uncommitted changes conflicting with the update."
    echo "Check 'git status' and resolve any conflicts, then re-run this script."
    exit 1
fi

echo ""
echo "Rebuilding STORM.app..."
bash "$SCRIPT_DIR/create_app.sh"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Update complete! Launch STORM normally."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
