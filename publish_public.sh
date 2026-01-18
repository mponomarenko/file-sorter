#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "" ]; then
  echo "Usage: $(basename "$0") /path/to/public-repo"
  exit 2
fi

TARGET_DIR="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR"
DENYLIST="$SRC_DIR/publish_denylist.txt"

if [ ! -f "$DENYLIST" ]; then
  echo "Missing denylist file: $DENYLIST"
  exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "rg is required for publish validation."
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required for publish copy."
  exit 1
fi

found=0
while IFS= read -r pattern; do
  if (
    cd "$SRC_DIR" && rg -n \
      --glob '!.git/**' \
      --glob '!.env' \
      --glob '!tests/private/**' \
      --glob '!publish_denylist.txt' \
      --glob '!.venv/**' \
      --glob '!**/__pycache__/**' \
      --glob '!node_modules/**' \
      -e "$pattern" .
  ); then
    echo "Pattern matched: $pattern"
    found=1
  fi
done < <(grep -vE '^\s*(#|$)' "$DENYLIST")

if [ "$found" -ne 0 ]; then
  echo "Publish aborted: private references detected."
  exit 1
fi

mkdir -p "$TARGET_DIR"
rsync -a \
  --exclude '.git' \
  --exclude 'tests/private' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.mypy_cache' \
  --exclude '.pytest_cache' \
  --exclude '*.ppk' \
  --exclude '*.pem' \
  --exclude '.env' \
  --exclude 'node_modules' \
  "$SRC_DIR/" "$TARGET_DIR/"

echo "Publish copy complete: $TARGET_DIR"
