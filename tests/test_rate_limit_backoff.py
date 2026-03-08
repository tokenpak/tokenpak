import pytest
import asyncio
from tokenpak.handlers.rate_limit import RateLimitBackoff, get_backoff


@pytest.mark.asyncio
async def test_backoff_retries_on_429():
    backoff = RateLimitBackoff(max_retries=2, base_wait=0.01, jitter_factor=0.0)
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
    backoff = RateLimitBackoff(max_retries=1, base_wait=0.01, jitter_factor=0.0)

    async def always_429():
        err = Exception("rate limited")
        err.status_code = 429
        raise err

    with pytest.raises(Exception, match="rate limited"):
        await backoff.execute(always_429)


def test_wait_time_increases_with_attempt():
    backoff = RateLimitBackoff(base_wait=1.0, jitter_factor=0.0)
    assert backoff.wait_time(0) < backoff.wait_time(1) < backoff.wait_time(2)


def test_singleton_returns_instance():
    b = get_backoff()
    assert isinstance(b, RateLimitBackoff)
    assert b is get_backoff()  # Same singleton
