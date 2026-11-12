#!/usr/bin/env bash
# Run the e39-rpi map toolkit (window by default; see --help for commands).
# Creates toolkit/.venv with its requirements on first use.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ] || [ "$HERE/requirements.txt" -nt "$VENV/.installed" ]; then
    python3 -c 'import tkinter' 2>/dev/null || { echo "Needs Tk for Python: sudo apt install python3-tk" >&2; exit 1; }
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$HERE/requirements.txt"
    touch "$VENV/.installed"
fi
# mapdata.schema (the map database format) comes from the backend's source.
export PYTHONPATH="$HERE:$HERE/../src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1   # log lines as they happen, also into a file
exec "$VENV/bin/python" -B -m e39maps_toolkit "$@"
