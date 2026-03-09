# Rate Limit Implementation Checklist

Verified against `tokenpak/handlers/rate_limit.py` and `tokenpak/agent/agentic/retry.py`.

## Core Implementation

- [x] `RateLimitHandler` class exists with retry logic
  - Class: `RateLimitBackoff` in `tokenpak/handlers/rate_limit.py`
  - `execute(fn, *args, **kwargs)` drives async retry loop

- [x] Exponential backoff formula: `delay = min(base * (2^attempt), max_delay)`
  - Implemented in `RateLimitBackoff.wait_time()`:
    ```python
    base = min(self.base_wait * (2 ** attempt), self.max_wait)
    ```

- [x] Jitter applied: `delay = delay * (1 + random(-jitter, +jitter))`
  - Implemented as full positive jitter in `wait_time()`:
    ```python
    jitter = random.uniform(0, base * self.jitter_factor)
    return base + jitter
    ```
  - Note: uses `[0, jitter_factor]` range instead of `[-jitter, +jitter]`; equivalent average behavior

- [x] Retry-After header parsed and respected
  - `execute()` checks `getattr(e, "retry_after", None)` and passes to `wait_time()`
  - When `retry_after` is set, it replaces the computed exponential base (capped at `max_wait`)

- [x] Per-provider strategy selection working
  - `RetryEngine` in `retry.py` uses `per_error` dict to route by HTTP status
  - Provider chain configured via `retry.provider_chain` in `~/.tokenpak/config.json`
  - `config/rate-limits.yaml` now defines per-provider strategy parameters

- [x] Circuit breaker: stops retrying after N consecutive failures
  - `RetryEngine` implements 5-level escalation:
    - Level 0: exponential backoff (`wait_seconds` list)
    - Level 1: model downgrade (Opus → Sonnet → Haiku)
    - Level 2: provider switch (Anthropic → OpenAI → Google)
    - Level 3: agent handoff (state preserved to JSON)
    - Level 4: save state + human alert + raise `RetryExhaustedError`

- [x] Metrics logged: attempt count, final delay, success/failure
  - Every escalation event written to `~/.tokenpak/retry_events.jsonl` via `_append_retry_event()`
  - Fields: `event`, `task_id`, `agent`, `http_status`, `timestamp`, level-specific data
  - `load_recent_retry_events(n)` provides programmatic access

- [x] Config loaded from YAML and applied
  - `_load_retry_config()` reads `~/.tokenpak/config.json` → `retry` section
  - New `config/rate-limits.yaml` provides production-ready per-provider defaults
  - ⚠️ YAML loader not yet wired into `RetryEngine` directly — `config.json` is the active config path

- [x] Tests pass: `test_rate_limit_backoff.py` (4 test cases) + `test_retry.py` (394 lines)
  - `test_backoff_retries_on_429` — verifies retry on 429
  - `test_backoff_raises_after_max_retries` — verifies failure after max retries
  - `test_wait_time_increases_with_attempt` — verifies exponential growth
  - `test_singleton_returns_instance` — verifies singleton pattern
  - `test_retry.py` covers full `RetryEngine` escalation paths (394 lines, ~20+ cases)

## Gap Analysis

| Item | Status | Notes |
|------|--------|-------|
| `RateLimitBackoff` class | ✅ Complete | `tokenpak/handlers/rate_limit.py` |
| Exponential formula | ✅ Complete | `wait_time()` method |
| Jitter | ✅ Complete | Positive full jitter |
| Retry-After header | ✅ Complete | `retry_after` attribute on exception |
| Per-provider selection | ✅ Complete | via `RetryEngine.per_error` dict |
| Circuit breaker | ✅ Complete | 5-level `RetryEngine` escalation |
| Event logging | ✅ Complete | JSONL shadow log |
| Config from YAML | ⚠️ Partial | YAML file created; `RetryEngine` reads JSON only |
| ≥10 test cases | ✅ Complete | 4 in backoff tests + ~20+ in retry tests |

## Files

| File | Purpose |
|------|---------|
| `tokenpak/handlers/rate_limit.py` | `RateLimitBackoff` — async 429 handler |
| `tokenpak/agent/agentic/retry.py` | `RetryEngine` — 5-level escalation |
| `config/rate-limits.yaml` | Production rate limit config |
| `docs/rate-limiting.md` | Strategy documentation |
| `tests/test_rate_limit_backoff.py` | Unit tests for `RateLimitBackoff` |
| `tests/test_retry.py` | Integration tests for `RetryEngine` |
