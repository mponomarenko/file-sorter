#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$ROOT")"
PROMPTS_DIR="${PROMPTS_DIR:-$PROJECT_ROOT/prompts}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/prompts/outputs}"
SUMMARY_FILE="${SUMMARY_FILE:-$OUTPUT_DIR/prompt_summary.json}"
SUMMARY_CSV="${SUMMARY_CSV:-$OUTPUT_DIR/prompt_summary.csv}"

usage() {
    echo "Usage: $0 <file> [additional analyze args...]" >&2
    echo "Runs cli/analyze.sh full once per prompt template found in $PROMPTS_DIR." >&2
    exit 1
}

[[ $# -lt 1 ]] && usage
TARGET_PATH="$1"
shift
USER_ARGS=("$@")

if [[ ! -f "$TARGET_PATH" ]]; then
    echo "Error: $TARGET_PATH is not a file" >&2
    exit 1
fi

if [[ ! -d "$PROMPTS_DIR" ]]; then
    echo "Error: Prompt directory not found: $PROMPTS_DIR" >&2
    exit 1
fi

shopt -s nullglob
prompt_files=("$PROMPTS_DIR"/*.prompt)
shopt -u nullglob

if [[ ${#prompt_files[@]} -eq 0 ]]; then
    echo "Error: No *.prompt files found in $PROMPTS_DIR" >&2
    exit 1
fi

# Baseline run without custom prompt
result_files=()
mkdir -p "$OUTPUT_DIR"
baseline_out="$OUTPUT_DIR/default.prompt.json"
echo "=================================================="
echo "[prompt-sweep] Using default prompt (baseline)"
"$ROOT"/analyze.sh full --json "$baseline_out" "${USER_ARGS[@]}" "$TARGET_PATH"
if [[ -f "$baseline_out" ]]; then
    result_files+=("$baseline_out")
else
    echo "[prompt-sweep] Warning: expected result file not found: $baseline_out" >&2
fi

for prompt in "${prompt_files[@]}"; do
    echo "=================================================="
    echo "[prompt-sweep] Using $(basename "$prompt")"
    out_json="$OUTPUT_DIR/$(basename "$prompt").json"
    "$ROOT"/analyze.sh full --json "$out_json" --ollama-prompt "$prompt" "${USER_ARGS[@]}" "$TARGET_PATH"
    if [[ -f "$out_json" ]]; then
        result_files+=("$out_json")
    else
        echo "[prompt-sweep] Warning: expected result file not found: $out_json" >&2
    fi
done

if [[ ${#result_files[@]} -eq 0 ]]; then
    echo "[prompt-sweep] No result files generated; skipping jq summary" >&2
    exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "[prompt-sweep] jq not available; skipping summary aggregation" >&2
    exit 0
fi

tmp_summary="$(mktemp)"
jq -n --arg analyzed "$TARGET_PATH" '
    [ inputs
      | {path: (.path // $analyzed),
         prompt: (input_filename | split("/")[-1] | sub("\\.prompt\\.json$"; "") | sub("\\.json$"; "")),
         result: .}
    ]
' "${result_files[@]}" > "$tmp_summary"
mv "$tmp_summary" "$SUMMARY_FILE"
echo "[prompt-sweep] Wrote summary to $SUMMARY_FILE"

jq -r '.[] | [.prompt, (.result.ai_best_response // .result.ai_classification.best.category // "Unknown")] | @csv' \
    "$SUMMARY_FILE" > "$SUMMARY_CSV"
echo "[prompt-sweep] Wrote CSV summary to $SUMMARY_CSV"
