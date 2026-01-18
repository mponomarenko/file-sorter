# Integration Tests

Integration tests verify the system works with real external services and APIs.

## Test Suites

### LM Studio Integration (`test_lmstudio_integration.py`)

Tests the OpenAI classifier with LM Studio (OpenAI-compatible local API).

**Requirements:**
- LM Studio running with a model loaded
- Network access to LM Studio endpoint

**Environment Variables:**
```bash
LMSTUDIO_URL=http://localhost:1234           # Required: LM Studio API endpoint
LMSTUDIO_MODEL=openai/gpt-oss-20b           # Required: model name
OLLAMA_URL=http://localhost:1234             # Required: for config initialization
```

**Run Tests:**
```bash
# Run all LM Studio tests
LMSTUDIO_URL=http://localhost:1234 \
LMSTUDIO_MODEL=openai/gpt-oss-20b \  # Required
OLLAMA_URL=http://localhost:1234 \
./test.sh tests/integration/test_lmstudio_integration.py -v

# Run specific test
LMSTUDIO_URL=http://localhost:1234 \
OLLAMA_URL=http://localhost:1234 \
./test.sh tests/integration/test_lmstudio_integration.py::test_folder_action_batch -v

# Run with verbose output
LMSTUDIO_URL=http://localhost:1234 \
OLLAMA_URL=http://localhost:1234 \
./test.sh tests/integration/test_lmstudio_integration.py -v -s
```

**Test Coverage:**
- ✅ Auto-detection of OpenAI-compatible API
- ✅ Endpoint availability checking
- ✅ Model listing
- ✅ Folder action decisions (meaningful names, generic names, projects)
- ✅ Hint handling
- ✅ Rules → AI delegation chain
- ✅ Rules final decisions (structural markers)
- ✅ Async file classification
- ✅ Batch processing (6 test cases, 100% accuracy)
- ✅ Token usage tracking
- ✅ Concurrent requests

**Expected Results:**
```
14 passed in ~40 seconds

Folder Action Results:
✓ Wedding-Photos-2024       → keep            (expected: keep)
✓ Downloads                 → disaggregate    (expected: disaggregate)
✓ Work-Contracts            → keep            (expected: keep)
✓ temp                      → disaggregate    (expected: disaggregate)
✓ MyProject                 → keep            (expected: keep)
✓ Misc                      → disaggregate    (expected: disaggregate)

Accuracy: 100%
```

## Adding New Integration Tests

### Template Structure

```python
import os
import pytest
from pathlib import Path

# Skip if service not available
pytestmark = pytest.mark.skipif(
    not os.getenv("SERVICE_URL"),
    reason="SERVICE_URL environment variable not set"
)

@pytest.fixture
def service_url():
    return os.getenv("SERVICE_URL")

def test_service_feature(service_url):
    # Test implementation
    pass
```

### Best Practices

1. **Skip gracefully** - Use `pytest.mark.skipif` for missing services
2. **Environment-driven** - Configure via environment variables
3. **Self-contained** - Don't depend on test order
4. **Clean up** - Reset state after tests if needed
5. **Document requirements** - List what's needed to run tests
6. **Meaningful assertions** - Test actual behavior, not just "doesn't crash"

## CI/CD Considerations

Integration tests are typically:
- ❌ NOT run in CI by default (require external services)
- ✅ Run manually before releases
- ✅ Run in staging environments with real services
- ✅ Skipped when services unavailable

To run in CI:
```yaml
# .github/workflows/integration.yml
- name: Run integration tests
  if: env.LMSTUDIO_URL != ''
  run: |
    LMSTUDIO_URL=${{ secrets.LMSTUDIO_URL }} \
    ./test.sh tests/integration/ -v
```

## Troubleshooting

### Tests Skipped
```
SKIPPED [1] test_lmstudio_integration.py: LMSTUDIO_URL environment variable not set
```
**Solution:** Set required environment variables

### Connection Refused
```
ConnectionError: [Errno 111] Connection refused
```
**Solution:** 
- Verify service is running
- Check URL/port is correct
- Check firewall/network settings

### Model Not Found
```
WARNING: OpenAI endpoint reachable but model 'gpt-3.5-turbo' not found
```
**Solution:** Set `LMSTUDIO_MODEL` to an available model or let auto-detection choose

### Timeout
```
httpx.ReadTimeout: timed out
```
**Solution:**
- Service may be slow/overloaded
- Increase timeout in classifier initialization
- Check model is loaded in LM Studio

## Performance Benchmarks

Based on LM Studio with `openai/gpt-oss-20b`:

| Operation | Time | Tokens |
|-----------|------|--------|
| Auto-detection (cached) | <1ms | 0 |
| Auto-detection (probe) | ~100ms | 0 |
| Folder decision | ~100-150ms | ~350 |
| File classification | ~200-500ms | ~400-800 |
| Concurrent (5 requests) | ~500ms | ~1750 |

Token costs are tracked in logs for analysis.
