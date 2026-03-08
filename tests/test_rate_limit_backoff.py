"""
Tests for tokenpak.handlers.rate_limit — RateLimitBackoff (async) and RateLimitBackoffSync.
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from tokenpak.handlers.rate_limit import RateLimitBackoff, RateLimitBackoffSync, get_backoff, get_backoff_sync


# ── Async tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backoff_retries_on_429():
    backoff = RateLimitBackoff(max_retries=2, base_wait=0.01)
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            err = Exception("rate limited")
            err.status_code = 429
            raise err
        return "ok"

    result = await backoff.execute(flaky)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_backoff_raises_after_max_retries():
    backoff = RateLimitBackoff(max_retries=1, base_wait=0.01)

    async def always_429():
        err = Exception("rate limited")
        err.status_code = 429
        raise err

    with pytest.raises(Exception, match="rate limited"):
        await backoff.execute(always_429)


def test_wait_time_increases_with_attempt():
    backoff = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert backoff.wait_time(0) < backoff.wait_time(1) < backoff.wait_time(2)


def test_wait_time_respects_retry_after():
    backoff = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0, max_wait=120.0)
    # Retry-After=30 should be used as base
    wait = backoff.wait_time(0, retry_after=30.0)
    assert wait == pytest.approx(30.0, abs=1e-9)


def test_wait_time_caps_at_max():
    backoff = RateLimitBackoff(base_wait=1.0, max_wait=10.0, jitter_factor=0.0)
    # 2^10 = 1024, but max is 10
    assert backoff.wait_time(10) == pytest.approx(10.0, abs=1e-9)


@pytest.mark.asyncio
async def test_backoff_passes_through_non_429():
    backoff = RateLimitBackoff(max_retries=3, base_wait=0.01)

    async def server_error():
        err = Exception("internal server error")
        err.status_code = 500
        raise err

    with pytest.raises(Exception, match="internal server error"):
        await backoff.execute(server_error)


# ── Sync tests ────────────────────────────────────────────────────────────────

def test_sync_backoff_retries_on_429():
    backoff = RateLimitBackoffSync(max_retries=2, base_wait=0.01)
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return (429, {}, b'{"error": "rate limited"}')
        return (200, {}, b'{"result": "ok"}')

    status, headers, body = backoff.execute(flaky)
    assert status == 200
    assert call_count == 3


def test_sync_backoff_returns_429_after_max_retries():
    backoff = RateLimitBackoffSync(max_retries=1, base_wait=0.01)

    def always_429():
        return (429, {}, b'{"error": "rate limited"}')

    status, headers, body = backoff.execute(always_429)
    assert status == 429


def test_sync_passes_through_non_429():
    backoff = RateLimitBackoffSync(max_retries=3, base_wait=0.01)

    def server_error():
        return (500, {}, b'{"error": "server error"}')

    status, headers, body = backoff.execute(server_error)
    assert status == 500


def test_sync_respects_retry_after_header():
    backoff = RateLimitBackoffSync(max_retries=2, base_wait=1.0, jitter_factor=0.0)
    call_count = 0
    wait_times = []
    original_sleep = time.sleep

    import unittest.mock as mock
    with mock.patch('time.sleep') as mock_sleep:
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return (429, {"Retry-After": "5"}, b'{}')
            return (200, {}, b'{}')

        backoff.execute(flaky)
        # First retry should use Retry-After=5 as base
        assert mock_sleep.call_count >= 1
        first_wait = mock_sleep.call_args_list[0][0][0]
        assert first_wait == pytest.approx(5.0, abs=1e-9)


# ── Singleton tests ───────────────────────────────────────────────────────────

def test_get_backoff_returns_singleton():
    b1 = get_backoff()
    b2 = get_backoff()
    assert b1 is b2


def test_get_backoff_sync_returns_singleton():
    b1 = get_backoff_sync()
    b2 = get_backoff_sync()
    assert b1 is b2
