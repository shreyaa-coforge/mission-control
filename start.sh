#!/usr/bin/env bash
# Start the Mission Control console locally.
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

# --- .env --------------------------------------------------------------------
# Read one variable's value from .env (last match wins, quotes stripped).
env_value() {
  local line
  line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -n 1 || true)"
  line="${line#*=}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")"
  printf '%s' "$line"
}

if [ ! -f .env ]; then
  cp .env.example .env
  if grep -qE '^[[:space:]]*MODEL_PROVIDER[[:space:]]*=' .env; then
    tmp="$(mktemp)"
    sed -E 's/^[[:space:]]*MODEL_PROVIDER[[:space:]]*=.*/MODEL_PROVIDER=fake/' .env > "$tmp"
    mv "$tmp" .env
  else
    echo "MODEL_PROVIDER=fake" >> .env
  fi
  echo "No .env found. Created one in demo mode (MODEL_PROVIDER=fake)."
  echo "Add your OpenAI key to .env and set MODEL_PROVIDER=openai to use real models."
fi

# The app defaults to openai when MODEL_PROVIDER is unset.
provider="$(env_value MODEL_PROVIDER | tr '[:upper:]' '[:lower:]')"
provider="${provider:-openai}"

if [ "$provider" = "openai" ]; then
  missing=()
  for name in OPENAI_API_KEY OPENAI_MODEL_FAST OPENAI_MODEL_STRONG; do
    [ -n "$(env_value "$name")" ] || missing+=("$name")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    # Only variable names are printed, never values.
    echo "Warning: MODEL_PROVIDER=openai but these are empty in .env: ${missing[*]}" >&2
    echo "Fix: edit .env and fill them in, or set MODEL_PROVIDER=fake for demo mode." >&2
    exit 1
  fi
fi

port="${PORT:-8000}"
echo "Model provider: $provider"
echo "Console:        http://127.0.0.1:${port}/"
echo "File a report:  http://127.0.0.1:${port}/file"

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$port"
