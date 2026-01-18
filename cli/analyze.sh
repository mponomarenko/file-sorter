#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT="$(dirname "$ROOT")"
VENV_DIR="$PARENT/.venv"
ENV_FILE="$PARENT/.env"

# Load environment variables if .env exists
if [[ -f "$ENV_FILE" ]]; then
    echo "Loading environment from $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
fi

# Function to ensure Python environment is ready
setup_env() {
    if [[ ! -d "$VENV_DIR" || ! -x "$VENV_DIR/bin/python" ]]; then
        echo "Creating virtual environment at $VENV_DIR"
        python3 -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        python -m pip install --upgrade pip
        pip install -r "$PARENT/requirements.txt"
    else
        source "$VENV_DIR/bin/activate"
    fi
}

# Function to run in venv
run_venv() {
    local script=$1
    shift
    setup_env
    # Set DEBUG level logging for CLI tool
    # Set SOURCES to "/" for CLI context (rules define actual boundaries)
    # If --local flag is set, use localhost Ollama (assumes a remote/local instance is already running)
    local ollama_url="${OLLAMA_URL:-}"
    if [[ "$USE_LOCAL_OLLAMA" == "1" ]]; then
        ollama_url="http://localhost:11434"
    fi
    PYTHONPATH="$PARENT" LOGLEVEL=DEBUG SOURCES="/" OLLAMA_URL="$ollama_url" python "$ROOT/$script" "$@"
}

usage() {
    echo "Usage: $0 (rules|metadata|full) [options] path"
    echo
    echo "Commands:"
    echo "  rules    - Test rule matching for files/directories"
    echo "  metadata - Extract and display file metadata"
    echo "  full     - Run comprehensive analysis (rules, metadata, AI)"
    echo
    echo "Options:"
    echo "  --local                    - Use local Ollama at http://localhost:11434"
    echo "  --json FILE                - Write structured JSON payload to FILE"
    echo "  --no-ai                    - Skip AI classification (full only)"
    echo "  --ollama-url URL           - Override Ollama URL"
    echo "  --ollama-prompt FILE       - Custom system prompt (full only)"
    echo "  --expect-disaggregate NAME - Assert folder NAME is disaggregated (full only)"
    echo "  --expect-keep NAME         - Assert folder NAME is kept as unit (full only)"
    exit 1
}

# Main script
[[ $# -lt 2 ]] && usage

CMD=$1
shift

OLLAMA_URL_OVERRIDE=""
JSON_FILE=""
USE_LOCAL_OLLAMA=0

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            USE_LOCAL_OLLAMA=1
            shift
            ;;
        --ollama-url)
            [[ $# -lt 2 ]] && echo "Error: --ollama-url requires a URL" && exit 1
            OLLAMA_URL_OVERRIDE="$2"
            export OLLAMA_URL="$2"
            shift 2
            ;;
        --json)
            [[ $# -lt 2 ]] && echo "Error: --json requires a file path" && exit 1
            JSON_FILE="$2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

[[ $# -lt 1 ]] && usage

case "$CMD" in
    rules)
        SCRIPT="rules_analyzer.py"
        ;;
    metadata)
        SCRIPT="analyze_metadata.py"
        ;;
    full)
        SCRIPT="analyze_full.py"
        ;;
    *)
        usage
        ;;
esac

if [[ "$CMD" == "full" && "$USE_LOCAL_OLLAMA" -eq 0 && -z "${OLLAMA_URL:-}" && -z "$OLLAMA_URL_OVERRIDE" ]]; then
    echo "Error: OLLAMA_URL must be set (or pass --ollama-url/--local) for full analysis." >&2
    exit 1
fi

EXTRA_ARGS=()
if [[ -n "$JSON_FILE" ]]; then
    EXTRA_ARGS+=("--output-json" "$JSON_FILE")
fi

# Pass all remaining arguments to the Python script (including --expect-* flags)
run_venv "$SCRIPT" "${EXTRA_ARGS[@]}" "$@"
