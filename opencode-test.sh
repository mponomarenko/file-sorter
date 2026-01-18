#!/usr/bin/env bash
set -euo pipefail

# Load environment if .env exists
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

# Require explicit OLLAMA_URL in environment
# Format: url|workers|model (e.g., http://localhost:11434|4|gpt-oss:20b)
if [[ -z "${OLLAMA_URL:-}" ]]; then
    echo "OLLAMA_URL must be set in the environment (.env)" >&2
    echo "Format: url|workers|model" >&2
    echo "Example (Ollama): http://localhost:11434|4|gpt-oss:20b" >&2
    echo "Example (LM Studio): http://localhost:1234|2|openai/gpt-oss-20b" >&2
    exit 1
fi

# Split OLLAMA_URL by comma for multiple endpoints
IFS=',' read -ra ENTRIES <<< "$OLLAMA_URL"

echo "Testing OLLAMA endpoints..."
echo

for entry in "${ENTRIES[@]}"; do
    # Trim whitespace
    entry=$(echo "$entry" | xargs)
    
    # Parse url|workers|model
    IFS='|' read -ra PARTS <<< "$entry"
    url="${PARTS[0]}"
    model="${PARTS[2]:-}"
    
    if [[ -z "$model" ]]; then
        echo "ERROR: Model not specified in entry: $entry" >&2
        echo "Format: url|workers|model" >&2
        continue
    fi
    
    # JSON payload as heredoc
    PAYLOAD=$(cat <<EOF
{
  "model": "$model",
  "messages": [
    {"role": "system", "content": "Reply with strictly the word ok."},
    {"role": "user", "content": "hello"}
  ],
  "stream": false,
  "options": {"temperature": 0}
}
EOF
)
    echo "Testing $url ..."
    if curl -s -X POST "$url/api/chat" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" \
        --max-time 30 \
        --connect-timeout 10; then
        echo "✓ $url responded successfully"
    else
        echo "✗ $url failed or timed out"
    fi
    echo
done
