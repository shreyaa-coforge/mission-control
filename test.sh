#!/usr/bin/env bash
# Run the test suite. Extra arguments are passed to pytest.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# --- Python check (3.11+) ---------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found. Install Python 3.11 or newer." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Error: Python 3.11 or newer is required (found $(python3 --version 2>&1))." >&2
  exit 1
fi

# --- Virtual environment and dependencies -----------------------------------
if [ ! -d .venv ]; then
  echo "Creating virtual environment in .venv ..."
  python3 -m venv .venv
fi
echo "Installing requirements ..."
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo "Tests use a fake model: no network and no API key needed."
.venv/bin/python -m pytest -q "$@"
