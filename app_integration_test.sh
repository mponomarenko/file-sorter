#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [-u|--user USERNAME]" >&2
  echo "  -u, --user USERNAME   Run container as specified user (overrides .env)" >&2
  exit 1
}

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[integration] ERROR: Missing .env at $ENV_FILE (must define OLLAMA_URL)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

. "$DIR/ollama_util.sh"

if [[ -z "${OLLAMA_URL:-}" ]]; then
  echo "[integration] ERROR: OLLAMA_URL must be set to a reachable Ollama instance." >&2
  exit 1
fi
if ! ensure_ollama_available "$OLLAMA_URL"; then
  exit 1
fi

# Parse command line arguments
RUN_AS_USER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--user)
      RUN_AS_USER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

COMPOSE="$DIR/docker-compose.integration.yml"
export OLLAMA_URL

# If user specified, look up UID/GID and override .env
if [[ -n "$RUN_AS_USER" ]]; then
  if ! USER_UID=$(id -u "$RUN_AS_USER" 2>/dev/null); then
    echo "[integration] ERROR: User '$RUN_AS_USER' not found" >&2
    exit 1
  fi
  if ! USER_GID=$(id -g "$RUN_AS_USER" 2>/dev/null); then
    echo "[integration] ERROR: Could not determine GID for user '$RUN_AS_USER'" >&2
    exit 1
  fi
  echo "[integration] Running as user: $RUN_AS_USER (UID=$USER_UID, GID=$USER_GID)"
  export CONTAINER_UID="$USER_UID"
  export CONTAINER_GID="$USER_GID"
fi

# Check/fix permissions for host-mounted test data directory
# (Docker volumes it_work and it_target don't need host permission checks)
if [[ ! -d "$DIR/tests/data" ]]; then
  echo "[integration] ERROR: Required directory not found: $DIR/tests/data" >&2
  exit 1
fi

"$DIR/acl_check.sh" "$ENV_FILE" "$DIR/tests/data"

# Ensure integration volumes exist with permissive access so non-root containers can write reports
UID_VAL="${CONTAINER_UID:-1000}"
GID_VAL="${CONTAINER_GID:-1000}"
TMP_ROOT="$DIR/.tmp/integration"
IT_TARGET_DIR="${IT_TARGET_DIR:-$TMP_ROOT/target}"
IT_WORK_DIR="${IT_WORK_DIR:-$TMP_ROOT/work}"
mkdir -p "$IT_TARGET_DIR" "$IT_WORK_DIR"
chmod -R 0777 "$IT_TARGET_DIR" "$IT_WORK_DIR"
export IT_TARGET_DIR
export IT_WORK_DIR

echo "[integration] Running integration tests..."
set +e
docker compose -f "$COMPOSE" up --build --abort-on-container-exit itest
CODE=$?
set -e

echo "[integration] Shutting down stack..."
docker compose -f "$COMPOSE" down -v

exit $CODE
