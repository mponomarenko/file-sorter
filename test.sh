#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DIR/.venv"
ENV_FILE="$DIR/.env"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*"
}

# Check that dependencies are installed
if [[ ! -d "$VENV_DIR" || ! -x "$VENV_DIR/bin/python" ]]; then
  log "Error: Virtual environment not found at $VENV_DIR"
  log "Run init.sh first to install dependencies"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  log "Error: python3 is not installed. Run init.sh first."
  exit 1
fi

if ! command -v timeout >/dev/null 2>&1; then
  log "Error: timeout command not found. Run init.sh first."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  log "Error: Missing .env at $ENV_FILE (must define OLLAMA_URL)"
  exit 1
fi

# Load .env and require OLLAMA_URL
set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a
if [[ -z "${OLLAMA_URL:-}" ]]; then
  log "Error: OLLAMA_URL must be set in .env for tests."
  exit 1
fi

log "Activating virtualenv"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Running unit tests with pytest"
cd "$DIR"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH=.
"$VENV_DIR/bin/python" -m pytest -q tests/unit "$@"
