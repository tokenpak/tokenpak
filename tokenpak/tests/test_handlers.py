"""
Tests for tokenpak/handlers/ module
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Covers:
  - handlers package import sanity (__init__.py)
  - RateLimitBackoff: instantiation with defaults and custom params
  - RateLimitBackoff: wait_time() with retry_after override
  - RateLimitBackoff: exponential backoff scaling (deterministic, jitter=0)
  - RateLimitBackoff: max_wait ceiling enforcement
  - RateLimitBackoff: jitter adds non-negative amount
  - RateLimitBackoff: edge cases (attempt=0, negative attempt, zero base_wait)
"""

import pytest

from tokenpak import handlers
from tokenpak.proxy.handlers.rate_limit import RateLimitBackoff

# ── handlers package ────────────────────────────────────────────────────────


def test_handlers_package_importable():
    """The handlers package is importable."""
    assert handlers is not None


def test_rate_limit_module_importable():
    """RateLimitBackoff is importable from tokenpak.proxy.handlers.rate_limit."""
    assert RateLimitBackoff is not None


# ── RateLimitBackoff: initialization ───────────────────────────────────────


def test_defaults():
    """Default params: base_wait=1.0, max_wait=60.0, jitter_factor=0.1."""
    b = RateLimitBackoff()
    assert b.base_wait == 1.0
    assert b.max_wait == 60.0
    assert b.jitter_factor == 0.1


def test_custom_params():
    """Custom params are stored correctly."""
    b = RateLimitBackoff(base_wait=2.0, max_wait=30.0, jitter_factor=0.0)
    assert b.base_wait == 2.0
    assert b.max_wait == 30.0
    assert b.jitter_factor == 0.0


def test_deterministic_mode():
    """jitter_factor=0 creates a fully deterministic instance."""
    b = RateLimitBackoff(jitter_factor=0.0)
    assert b.jitter_factor == 0.0


# ── RateLimitBackoff: retry_after override ─────────────────────────────────


def test_retry_after_used_directly():
    """When retry_after is provided and <= max_wait, it is returned as-is."""
    b = RateLimitBackoff(jitter_factor=0.0)
    assert b.wait_time(attempt=0, retry_after=5.0) == 5.0


def test_retry_after_capped_at_max_wait():
    """retry_after exceeding max_wait is capped at max_wait."""
    b = RateLimitBackoff(max_wait=10.0, jitter_factor=0.0)
    assert b.wait_time(attempt=0, retry_after=999.0) == 10.0


def test_retry_after_exactly_max_wait():
    """retry_after equal to max_wait is returned unchanged."""
    b = RateLimitBackoff(max_wait=10.0, jitter_factor=0.0)
    assert b.wait_time(attempt=0, retry_after=10.0) == pytest.approx(10.0)


# ── RateLimitBackoff: exponential backoff (deterministic) ──────────────────


def test_exponential_attempt0():
    """Attempt 0 with jitter=0: base_wait * 2^0 == base_wait."""
    b = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert b.wait_time(0) == pytest.approx(1.0)


def test_exponential_attempt1():
    """Attempt 1 with jitter=0: base_wait * 2^1 == 2 * base_wait."""
    b = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert b.wait_time(1) == pytest.approx(2.0)


def test_exponential_attempt3():
    """Attempt 3 with jitter=0: 1.0 * 2^3 == 8.0."""
    b = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert b.wait_time(3) == pytest.approx(8.0)


def test_exponential_doubles_per_attempt():
    """Each successive attempt doubles the wait in deterministic mode."""
    b = RateLimitBackoff(base_wait=1.0, max_wait=1000.0, jitter_factor=0.0)
    waits = [b.wait_time(i) for i in range(5)]
    for i in range(1, 5):
        assert waits[i] == pytest.approx(waits[i - 1] * 2.0)


# ── RateLimitBackoff: max_wait ceiling ─────────────────────────────────────


def test_max_wait_ceiling():
    """High attempt numbers are capped at max_wait."""
    b = RateLimitBackoff(base_wait=1.0, max_wait=10.0, jitter_factor=0.0)
    assert b.wait_time(attempt=100) == pytest.approx(10.0)


def test_max_wait_never_exceeded_with_jitter():
    """With jitter enabled, result never exceeds max_wait."""
    b = RateLimitBackoff(base_wait=1.0, max_wait=10.0, jitter_factor=0.5)
    for attempt in range(20):
        assert b.wait_time(attempt) <= b.max_wait + 1e-9


# ── RateLimitBackoff: jitter ────────────────────────────────────────────────


def test_jitter_non_negative():
    """Jitter never produces a wait_time below the no-jitter baseline."""
    b_jitter = RateLimitBackoff(base_wait=1.0, max_wait=60.0, jitter_factor=0.5)
    b_no_jitter = RateLimitBackoff(base_wait=1.0, max_wait=60.0, jitter_factor=0.0)
    for attempt in range(10):
        base = b_no_jitter.wait_time(attempt)
        jittered = b_jitter.wait_time(attempt)
        assert jittered >= base - 1e-9


# ── RateLimitBackoff: edge cases ───────────────────────────────────────────


def test_zero_base_wait():
    """base_wait=0 always yields 0 regardless of attempt (no jitter)."""
    b = RateLimitBackoff(base_wait=0.0, jitter_factor=0.0)
    assert b.wait_time(0) == 0.0
    assert b.wait_time(5) == 0.0


def test_negative_attempt():
    """Negative attempt yields sub-base wait (2^negative < 1)."""
    b = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert b.wait_time(-1) == pytest.approx(0.5)  # 1.0 * 2^-1


def test_return_type_is_float():
    """wait_time always returns a float."""
    b = RateLimitBackoff(jitter_factor=0.0)
    assert isinstance(b.wait_time(0), float)
    assert isinstance(b.wait_time(0, retry_after=5.0), float)
