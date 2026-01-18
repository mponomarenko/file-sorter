#!/usr/bin/env bash
set -euo pipefail
: "${MODE:=all}"
: "${SOURCES:=/sources}"; : "${MAIN_TARGET:=/target}"; : "${REPORT_DIR:=/target/_reports}"
: "${DB_PATH:=/work/catalog.sqlite}"; : "${RELINK_WITH_REFLINK:=true}"
: "${MAX_CONTENT_PEEK:=1024}"; : "${SCAN_WORKERS:=8}"; : "${HASH_WORKERS:=8}"; : "${OLLAMA_WORKERS:=4}"; : "${MOVE_WORKERS:=4}"
: "${DB_BATCH_SIZE:=500}"; : "${OLLAMA_TIMEOUT:=120}"

if [[ "${CLASSIFIER:-ollama}" != "manual" && -z "${OLLAMA_URL:-}" ]]; then
  echo "[FATAL] OLLAMA_URL must be set for AI classifiers" >&2
  echo "Format: url|workers|model" >&2
  echo "Example (Ollama): http://localhost:11434|4|gpt-oss:20b" >&2
  echo "Example (LM Studio): http://localhost:1234|2|openai/gpt-oss-20b" >&2
  exit 1
fi

[ -d "$MAIN_TARGET" ] || { echo "[FATAL] MAIN_TARGET not mounted: $MAIN_TARGET" >&2; exit 1; }
mkdir -p "$REPORT_DIR" 2>/dev/null || true

# Wrap execution with /usr/bin/time for memory tracking
exec /usr/bin/time -v python -m app.orchestrator "$MODE"
