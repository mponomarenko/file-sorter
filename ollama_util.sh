#!/usr/bin/env bash
set -euo pipefail

# Check that a reachable Ollama endpoint responds to /api/version.
# Usage: ensure_ollama_available "http://remote-ollama:11434"
# Also handles multiple URLs separated by commas: "http://host1:11434,http://host2:11434"
ensure_ollama_available() {
  local urls="${1:-}"
  if [[ -z "$urls" ]]; then
    echo "[ollama] ERROR: OLLAMA_URL is not set" >&2
    return 1
  fi

  # Split by comma and check each URL
  IFS=',' read -ra URL_ARRAY <<< "$urls"
  local any_success=0
  
  for url in "${URL_ARRAY[@]}"; do
    # Extract just the URL part (before | if worker count specified)
    local base_url="${url%%|*}"
    base_url="$(echo "$base_url" | xargs)" # trim whitespace
    
    if curl -fsS "${base_url%/}/api/version" >/dev/null 2>&1; then
      echo "[ollama] OK: ${base_url} is reachable."
      any_success=1
    else
      echo "[ollama] WARNING: ${base_url} is not reachable or not responding as Ollama." >&2
    fi
  done
  
  if [[ $any_success -eq 1 ]]; then
    return 0
  fi

  echo "[ollama] ERROR: None of the Ollama URLs are reachable." >&2
  return 1
}
