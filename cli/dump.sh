#!/usr/bin/env bash
set -euo pipefail

# Wrapper script for dump.py to find database automatically

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${DB_PATH:-}"

# Try to find database
if [[ -z "$DB_PATH" ]]; then
    # Check common locations
    if [[ -f "$HOME/tmp-out/file-sorter-prod/catalog.sqlite" ]]; then
        DB_PATH="$HOME/tmp-out/file-sorter-prod/catalog.sqlite"
    elif [[ -f "/work/catalog.sqlite" ]]; then
        DB_PATH="/work/catalog.sqlite"
    elif [[ -f "./catalog.sqlite" ]]; then
        DB_PATH="./catalog.sqlite"
    fi
fi

if [[ -z "$DB_PATH" ]]; then
    echo "Error: Cannot find database file." >&2
    echo "Provide DB_PATH environment variable or path as first argument." >&2
    echo "" >&2
    echo "Usage: $0 [database_path] folder_path [--verbose]" >&2
    echo "   Or: DB_PATH=/path/to/catalog.sqlite $0 folder_path [--verbose]" >&2
    exit 1
fi

# If first arg is a file path (database), shift it
if [[ $# -gt 0 ]] && [[ -f "$1" ]]; then
    DB_PATH="$1"
    shift
fi

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 [database_path] folder_path [--verbose]" >&2
    echo "" >&2
    echo "Database: $DB_PATH" >&2
    exit 1
fi

echo "Using database: $DB_PATH" >&2
echo "" >&2

python3 "$SCRIPT_DIR/dump.py" "$DB_PATH" "$@"
