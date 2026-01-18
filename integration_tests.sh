#!/usr/bin/env bash
set -euo pipefail

# Run integration tests that require Ollama
# This script runs integration tests inside the compose stack defined in docker-compose.integration.yml.
# Expect an Ollama instance to be reachable at OLLAMA_URL; no local container is started.

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[integration_tests] ERROR: Missing .env at $ENV_FILE (must define OLLAMA_URL)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

. "$DIR/ollama_util.sh"

if [[ -z "${OLLAMA_URL:-}" ]]; then
  echo "[integration_tests] ERROR: OLLAMA_URL must be set to a reachable Ollama instance." >&2
  exit 1
fi
if ! ensure_ollama_available "$OLLAMA_URL"; then
  exit 1
fi

fail_if_cleaner_running() {
  # Avoid reusing stale cleaner containers that may hold old code or mounts
  local running
  running="$(docker ps --format '{{.Names}}' | grep -E '^(data-cleaner|data-cleaner-it|file-sorter-container)$' || true)"
  if [[ -n "$running" ]]; then
    echo "[integration_tests] Found running cleaner container(s): $running" >&2
    echo "[integration_tests] Stop them before running integration tests." >&2
    exit 1
  fi
}

TMP_ROOT="$DIR/.tmp/integration"
IT_TARGET_DIR="${IT_TARGET_DIR:-$TMP_ROOT/target}"
IT_WORK_DIR="${IT_WORK_DIR:-$TMP_ROOT/work}"
mkdir -p "$IT_TARGET_DIR" "$IT_WORK_DIR"
# Ensure writable for container user
chmod -R 0777 "$IT_TARGET_DIR" "$IT_WORK_DIR"

export OLLAMA_URL
export IT_TARGET_DIR
export IT_WORK_DIR

fail_if_cleaner_running

echo "[integration_tests] Running pytest integration tests via compose..."
set +e
docker compose -f "$DIR/docker-compose.integration.yml" up --build --remove-orphans --abort-on-container-exit --exit-code-from itest itest
CODE=$?
set -e

# Clean up the test container
docker compose -f "$DIR/docker-compose.integration.yml" rm -sf itest >/dev/null 2>&1 || true

if [[ $CODE -eq 0 ]]; then
  echo "[integration_tests] ✓ All integration tests passed"
else
  echo "[integration_tests] ✗ Some integration tests failed (exit code: $CODE)" >&2
fi

exit $CODE
