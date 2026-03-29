# TokenPak Testing Guide

## Overview

TokenPak uses pytest for unit testing, with structured test organization and coverage tracking. This guide covers how to run, write, and maintain tests — especially Phase 2 coverage additions.

## Quick Start

### Run all tests
```bash
pytest tests/ -v
```

### Run Phase 2 tests (validation + error + cache)
```bash
pytest tests/test_validation*.py tests/test_error*.py tests/test_cache*.py -v
```

### Run with coverage
```bash
pytest tests/ --cov=tokenpak --cov-report=html
open htmlcov/index.html
```

### Run a single test file
```bash
pytest tests/test_validation.py -v
```

### Run tests matching a pattern
```bash
pytest -k "test_schema" -v
```

## Test Organization

```
tests/
├── test_validation.py          # Phase 2: Input validation, schemas
├── test_error_handling.py       # Phase 2: Error recovery, logging
├── test_cache.py                # Phase 2: Cache behavior, TTL, concurrency
├── test_adapters.py             # Core: Adapter implementations
├── test_routing.py              # Core: Provider routing logic
├── test_compression.py          # Core: Token compression
├── test_integration.py          # Integration tests (slow)
└── conftest.py                  # Shared fixtures
```

## Test Markers

Tests are marked with `@pytest.mark` to enable selective running:

| Marker | Use Case | Run Command |
|--------|----------|----------|
| `unit` | Fast unit tests | `pytest -m unit` |
| `integration` | Requires external services | `pytest -m integration` |
| `slow` | >1 second per test | `pytest -m "not slow"` |
| `phase2` | Phase 2 coverage additions | `pytest -m phase2` |
| `xfail` | Known failures (expected) | Displayed in report |

### Run unit tests only
```bash
pytest -m "not integration and not slow" -v
```

### Run Phase 2 tests with coverage
```bash
pytest -m phase2 \
  --cov=tokenpak.validation \
  --cov=tokenpak.error_handling \
  --cov=tokenpak.cache \
  --cov-report=term-missing
```

## Phase 2 Test Scope

Phase 2 adds comprehensive testing for three critical modules:

### Validation (`tests/test_validation.py`)

Tests input validation, schema enforcement, and type checking.

**Coverage areas:**
- Valid config parsing
- Invalid/missing required fields
- Type coercion (str → int, bool, etc.)
- Default value handling
- Nested object validation
- Array/list validation

**Example:**
```python
@pytest.mark.phase2
def test_schema_validation_missing_required_field():
    """Invalid config missing 'provider' field should raise ValidationError."""
    config = {"model": "gpt-4"}  # Missing required 'provider'
    with pytest.raises(ValidationError):
        validate_config(config)
```

### Error Handling (`tests/test_error_handling.py`)

Tests error recovery, logging, and context propagation.

**Coverage areas:**
- API errors (timeout, auth failure, rate limit)
- Fallback logic (retry, alternative provider)
- Error logging and context
- Circuit breaker behavior
- Graceful degradation

**Example:**
```python
@pytest.mark.phase2
def test_retry_on_timeout():
    """Timeout should trigger automatic retry."""
    with mock.patch('requests.post', side_effect=Timeout()):
        result = call_with_retry(max_retries=2)
        assert mock.patch.call_count == 2
```

### Cache Management (`tests/test_cache.py`)

Tests cache hit/miss behavior, TTL expiry, and concurrent access.

**Coverage areas:**
- Cache hit (key found, fresh)
- Cache miss (key not found or expired)
- TTL expiry handling
- Cache invalidation
- LRU eviction
- Concurrent access / race conditions
- Size limits

**Example:**
```python
@pytest.mark.phase2
def test_cache_ttl_expiry():
    """Item should expire after TTL."""
    cache = LRUCache(ttl_seconds=1)
    cache.set("key", "value")
    assert cache.get("key") == "value"
    
    time.sleep(1.1)  # Wait past TTL
    assert cache.get("key") is None
```

## Writing Tests

### Test Structure

```python
import pytest
from unittest import mock
from tokenpak.validation import validate_config
from tokenpak.errors import ValidationError

@pytest.mark.phase2
def test_meaningful_name_describes_behavior():
    """
    Docstring: 1-2 sentences explaining what is being tested and why.
    """
    # Arrange: Set up test data and mocks
    config = {"provider": "anthropic", "model": "claude-3-sonnet"}
    
    # Act: Execute the code under test
    result = validate_config(config)
    
    # Assert: Verify expected behavior
    assert result.is_valid
    assert result.provider == "anthropic"
```

### Fixtures (Reusable Setup)

Define shared test data in `conftest.py`:

```python
@pytest.fixture
def valid_config():
    """A minimal valid TokenPak config."""
    return {
        "provider": "anthropic",
        "model": "claude-3-sonnet",
        "max_tokens": 4096,
    }

@pytest.fixture
def mock_api_response():
    """Mock a successful API response."""
    return {
        "id": "msg_123",
        "content": "Hello, world!",
        "usage": {"input_tokens": 10, "output_tokens": 5}
    }
```

Use in tests:
```python
def test_with_fixture(valid_config, mock_api_response):
    result = process_config(valid_config)
    assert result is not None
```

### Mocking External Dependencies

```python
from unittest import mock

@pytest.mark.phase2
def test_with_mock():
    """Test in isolation by mocking external calls."""
    with mock.patch('tokenpak.adapters.anthropic.call_api') as mock_call:
        mock_call.return_value = {"content": "Mocked response"}
        
        result = adapter.get_completion("Hello")
        
        assert result == "Mocked response"
        mock_call.assert_called_once()
```

## Coverage Reports

### Understand the HTML Report

```bash
pytest tests/ --cov=tokenpak --cov-report=html
open htmlcov/index.html
```

**Red lines** = Not covered (no test path)
**Yellow lines** = Partially covered (some branches not executed)
**Green lines** = Fully covered

### Coverage by Module

```bash
pytest tests/ --cov=tokenpak --cov-report=term-missing
```

Example output:
```
Name                                   Stmts   Miss Cover   Missing
------------------------------------------------------------------------
tokenpak/__init__.py                      5      0   100%
tokenpak/adapters.py                     60      8    87%   120-125,134
tokenpak/validation.py                   45      3    93%   78,102,115
tokenpak/cache.py                        55     12    78%   45-60,88-92
------------------------------------------------------------------------
TOTAL                                   165     23    86%
```

**Missing column** shows line numbers not executed by any test.

### Find Untested Code Paths

1. Look for high "Miss" counts in critical modules
2. Read the "Missing" line numbers
3. Add tests to cover those paths
4. Re-run coverage to verify improvement

## Continuous Integration

### GitHub Actions

Coverage is checked automatically on every PR:

1. **Phase 2 tests run** on Python 3.11
2. **Tier-1 gate enforced** — fails if validation+error+cache < 50%
3. **Coverage report uploaded** to Codecov
4. **Delta comment posted** on PR (coverage change)

**On PR merge,** coverage baseline is updated for next comparison.

### Pre-commit Hook

Local coverage check before every commit:

```bash
git commit  # Automatically runs coverage-check.sh
```

- **Warns** if coverage < 45%
- **Fails** if coverage < 40%
- **Override:** `git commit --no-verify`

## Common Issues & Fixes

### Coverage not updating?

**Check 1:** Are you running the right test file?
```bash
pytest tests/test_validation.py -v  # Should run the tests you added
```

**Check 2:** Is the code path being executed?
```bash
pytest tests/test_validation.py --cov=tokenpak.validation --cov-report=term-missing
# Check "Missing" lines — add tests for those
```

**Check 3:** Is coverage being measured correctly?
```bash
# Regenerate coverage DB
rm -rf .coverage htmlcov/
pytest tests/ --cov=tokenpak --cov-report=html
```

### Test is flaky (fails randomly)?

1. **Look for:** Time-dependent code, random data, external API calls
2. **Fix with:** Mocks, fixtures, `freezegun` for time, `@pytest.mark.flaky`
3. **Never:** Use `@pytest.mark.skip` to hide flakiness

### Test runs too slowly?

1. Mark as `@pytest.mark.slow`
2. Run fast tests with: `pytest -m "not slow"`
3. Run slow tests separately in CI or nightly

## Best Practices

✅ **Do:**
- Write tests **before** implementing features (TDD)
- Use descriptive test names (`test_cache_hit_returns_fresh_value`)
- Mock external API calls
- Test edge cases (empty inputs, null values, exceptions)
- Use fixtures for reusable setup
- Keep tests focused (one behavior per test)

❌ **Don't:**
- Test implementation details (test behavior, not code)
- Skip flaky tests (fix them instead)
- Disable coverage for convenience
- Write overly complex tests (refactor into smaller pieces)
- Ignore test failures (fix the bug or mark as xfail)

## Further Reading

- [pytest documentation](https://docs.pytest.org)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
- [coverage.py](https://coverage.readthedocs.io/)
- TokenPak CONTRIBUTING.md (project-specific guidelines)

---

## Known Issues & Historical Root Causes

### proxy_v4.py — Import Path (Resolved 2026-03-26)

**Symptom:** Pytest collection hangs or `FileNotFoundError` on test files that reference `proxy_v4.py`.

**Affected test files:**
- `tests/test_proxy_error_paths.py`
- `tests/test_classifier_first_router.py`
- `tests/test_proxy_v4_cache_stats.py`
- `tests/test_proxy_v4_upstream_routes.py`
- `tests/test_ingest_proxy_v4.py`

**Root cause:** These tests load `proxy_v4.py` directly as a standalone module (not a package import) using a hardcoded path:
```python
_PROXY_V4_PATH = Path(__file__).parent.parent / "packages/core/proxy_v4.py"
```
If `proxy_v4.py` is missing from `packages/core/` or if a stale/wrong-size copy exists at vault root, tests fail with `FileNotFoundError` at collection time, causing apparent "hangs".

**Fix:** The canonical file is `packages/core/proxy_v4.py` (211,859 bytes as of 2026-03-25). Do NOT use the vault-root `proxy_v4.py` (smaller, older copy — stale). If tests fail to collect, verify:
```bash
ls -la packages/core/proxy_v4.py   # should be ~211KB
```

**Resolution:** Removed stale vault-root `proxy_v4.py`; canonical path is `packages/core/proxy_v4.py`.

### Collection speed baseline

- Expected: `pytest --collect-only tests/ -q` completes in **7–10 seconds** (1175 tests)
- If collection exceeds 30s, check for hanging fixture imports (see above)
- Run `pytest --collect-only --trace-config` to identify slow plugin/fixture loads

---

**Last updated:** 2026-03-26 (Phase 2 integration + proxy_v4 root cause docs)
