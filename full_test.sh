#!/usr/bin/env bash
set -euo pipefail

# Public test suite: uses repo-local fixtures only.

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DIR/.env"

echo "========================================"
echo "FULL TEST SUITE (PUBLIC)"
echo "========================================"
echo

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[full_test] ERROR: Missing .env at $ENV_FILE (must define OLLAMA_URL)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

if [[ -z "${OLLAMA_URL:-}" ]]; then
  echo "[full_test] ERROR: OLLAMA_URL must be set to a reachable Ollama instance." >&2
  exit 1
fi

FAILED=0
FAILED_TESTS=()

run_test() {
  local name="$1"
  shift
  echo -e "${YELLOW}>>> Running: $name${NC}"
  echo "Command: $*"
  echo
  
  if "$@"; then
    echo -e "${GREEN}✓ $name PASSED${NC}"
    echo
    return 0
  else
    CODE=$?
    echo -e "${RED}✗ $name FAILED (exit code: $CODE)${NC}"
    echo
    FAILED=1
    FAILED_TESTS+=("$name")
    return $CODE
  fi
}

# 0. Lint Check (type checking)
run_test "Lint Check (mypy)" "$DIR/lint.sh" || true

# 1. Unit Tests
run_test "Unit Tests" "$DIR/test.sh" tests/unit/ || true

# 2. Integration Tests (with Ollama in Docker)
run_test "Integration Tests" "$DIR/integration_tests.sh" || true

# 3. CLI Tests (analyze.sh, repo-local fixtures only)
run_test "CLI Test (pidgin fixture)" \
  "$DIR/cli/analyze.sh" full \
  "$DIR/test_data/pidgin_portable/Data/config.txt" \
  || true

# 4. Full App Integration Test (Docker end-to-end)
run_test "Full App Integration Test (Docker)" "$DIR/app_integration_test.sh" || true

echo "========================================"
if [[ $FAILED -eq 0 ]]; then
  echo -e "${GREEN}✓ ALL TESTS PASSED${NC}"
  exit 0
else
  echo -e "${RED}✗ SOME TESTS FAILED${NC}"
  echo
  echo "Failed tests:"
  for test in "${FAILED_TESTS[@]}"; do
    echo -e "  ${RED}✗${NC} $test"
  done
  exit 1
fi
