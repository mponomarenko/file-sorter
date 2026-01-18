#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [-u|--user USERNAME]" >&2
  echo "  -u, --user USERNAME   Run container as specified user (overrides .env)" >&2
  exit 1
}

ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

ENV_FILE="$ROOT/.env"
COMPOSE="$ROOT/docker-compose.yml"
OUT_DIR_DEFAULT="$HOME/tmp-out/file-sorter-prod"
OUT_DIR="${OUT_DIR:-$OUT_DIR_DEFAULT}"

# Load environment from .env so we can read OLLAMA_* and other settings
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# If user specified, look up UID/GID and override .env
if [[ -n "$RUN_AS_USER" ]]; then
  if ! USER_UID=$(id -u "$RUN_AS_USER" 2>/dev/null); then
    echo "[run] ERROR: User '$RUN_AS_USER' not found" >&2
    exit 1
  fi
  if ! USER_GID=$(id -g "$RUN_AS_USER" 2>/dev/null); then
    echo "[run] ERROR: Could not determine GID for user '$RUN_AS_USER'" >&2
    exit 1
  fi
  echo "[run] Running as user: $RUN_AS_USER (UID=$USER_UID, GID=$USER_GID)"
  export CONTAINER_UID="$USER_UID"
  export CONTAINER_GID="$USER_GID"
fi

echo "[run] Preflight checks..."
command -v docker >/dev/null 2>&1 || { echo "Error: docker not found. Run init.sh first." >&2; exit 1; }
if ! docker compose version >/dev/null 2>&1; then
  echo "Error: docker compose (v2) not found. Run init.sh first." >&2; exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[run] Missing .env at $ENV_FILE" >&2; exit 1
fi

echo "[run] Using OUT_DIR: $OUT_DIR"

# Check/fix permissions for non-root container
"$ROOT/acl_check.sh" "$ENV_FILE" "$OUT_DIR"

CLASSIFIER_KIND_LOWER="$(printf '%s' "${CLASSIFIER_KIND:-}" | tr '[:upper:]' '[:lower:]')"
if [[ "$CLASSIFIER_KIND_LOWER" != "manual" && -z "${OLLAMA_URL:-}" ]]; then
  echo "[run] ERROR: OLLAMA_URL must be set when using AI classifiers (CLASSIFIER_KIND=$CLASSIFIER_KIND_LOWER)" >&2
  exit 1
fi

echo "[run] CLASSIFIER_KIND=${CLASSIFIER_KIND:-} - expecting Ollama at OLLAMA_URL (no local container bootstrap)"

TARGET="$OUT_DIR" docker compose --env-file "$ENV_FILE" -f "$COMPOSE" stop || true
TARGET="$OUT_DIR" docker compose --env-file "$ENV_FILE" -f "$COMPOSE" build
# Run classification/report pipeline (non-destructive; mover disabled):
echo "[run] Starting cleaner..."
LOGLEVEL=INFO TARGET="$OUT_DIR" docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up --abort-on-container-exit cleaner

# OLLAMA_URL format: url|workers|model
# Example (Ollama): OLLAMA_URL=http://localhost:11434|4|gpt-oss:20b
# Example (LM Studio): OLLAMA_URL=http://localhost:1234|2|openai/gpt-oss-20b

# Historical dry-run toggles removed; all operations are non-destructive now.
