#!/usr/bin/env bash
# Runs the tracker, creating/updating the venv on first use so you never have
# to think about it. Usage: ./run.sh [adsb_tracker.py args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
STAMP="$VENV_DIR/.deps-installed"

if [ ! -d "$VENV_DIR" ]; then
    echo "Setting up virtual environment (first run only)..."
    python3 -m venv "$VENV_DIR"
fi

if [ ! -f "$STAMP" ] || [ "$SCRIPT_DIR/requirements.txt" -nt "$STAMP" ]; then
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/scripts/patch_pyrtlsdr.py"
    touch "$STAMP"
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/adsb_tracker.py" "$@"
