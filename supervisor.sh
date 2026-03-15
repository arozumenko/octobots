#!/usr/bin/env bash
# Octobots Supervisor — delegates to Python implementation.
# Kept as a convenience wrapper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Find Python — prefer project venv
if [[ -f "venv/bin/python" ]]; then
    PYTHON="venv/bin/python"
elif [[ -f ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" "$SCRIPT_DIR/scripts/supervisor.py" "$@"
