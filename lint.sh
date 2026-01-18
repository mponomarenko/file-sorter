#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DIR/.venv"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*"
}

if [[ ! -d "$VENV_DIR" ]]; then
  log "ERROR: virtualenv not found at $VENV_DIR. Run init.sh first."
  exit 1
fi

log "Activating virtualenv"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Running mypy type checker on app/ and cli/"
mypy app/ cli/

EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
  log "✓ Linting passed"
else
  log "✗ Linting failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
