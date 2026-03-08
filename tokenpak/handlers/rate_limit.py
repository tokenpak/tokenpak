"""
Rate limit backoff handler for TokenPak proxy.
Implements exponential backoff with jitter to handle HTTP 429 responses.

Provides two interfaces:
  - RateLimitBackoff: async version for use with agentic/async proxy code
  - RateLimitBackoffSync: sync version for use in proxy_v4.py (http.client based)
"""
import asyncio
import random
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimitBackoff:
    """Exponential backoff with jitter for 429 responses (async)."""

    def __init__(
        self,
        max_retries: int = 4,
        base_wait: float = 1.0,
        max_wait: float = 60.0,
        jitter_factor: float = 0.2,
    ):
        self.max_retries = max_retries
        self.base_wait = base_wait
        self.max_wait = max_wait
        self.jitter_factor = jitter_factor

    def wait_time(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate wait time for given attempt (0-indexed)."""
        if retry_after:
            # Respect server's Retry-After header
            base = min(retry_after, self.max_wait)
        else:
            base = min(self.base_wait * (2 ** attempt), self.max_wait)
        jitter = random.uniform(0, base * self.jitter_factor)
        return base + jitter

    async def execute(self, fn, *args, **kwargs):
        """Execute async fn with backoff on 429."""
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                status = getattr(e, "status_code", None) or getattr(e, "status", None)
                if status == 429 and attempt < self.max_retries:
                    retry_after = getattr(e, "retry_after", None)
                    wait = self.wait_time(attempt, retry_after)
                    logger.warning(
                        f"Rate limited (attempt {attempt + 1}/{self.max_retries}). "
                        f"Waiting {wait:.1f}s..."
                    )
                    await asyncio.sleep(wait)
                    last_exc = e
                else:
                    raise
        raise last_exc


class RateLimitBackoffSync:
    """Synchronous exponential backoff with jitter for 429 responses.

    Designed for use in proxy_v4.py which uses http.client (synchronous).
    Returns (status, headers, body) after retries or raises on exhaustion.
    """

    def __init__(
        self,
        max_retries: int = 4,
        base_wait: float = 1.0,
        max_wait: float = 60.0,
        jitter_factor: float = 0.2,
    ):
        self.max_retries = max_retries
        self.base_wait = base_wait
        self.max_wait = max_wait
        self.jitter_factor = jitter_factor

    def wait_time(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate wait time for given attempt (0-indexed)."""
        if retry_after:
            base = min(float(retry_after), self.max_wait)
        else:
            base = min(self.base_wait * (2 ** attempt), self.max_wait)
        jitter = random.uniform(0, base * self.jitter_factor)
        return base + jitter

    def execute(self, fn, *args, **kwargs):
        """Execute sync fn with backoff on 429.

        fn must return (status_code, headers_dict, body_bytes).
        Retries transparently on 429; other statuses are returned as-is.
        """
        last_result = None
        for attempt in range(self.max_retries + 1):
            result = fn(*args, **kwargs)
            status, headers, body = result
            if status != 429 or attempt >= self.max_retries:
                return result
            # Parse Retry-After header if present
            retry_after = None
            if isinstance(headers, dict):
                ra = headers.get("Retry-After") or headers.get("retry-after")
                if ra:
                    try:
                        retry_after = float(ra)
                    except (ValueError, TypeError):
                        pass
            wait = self.wait_time(attempt, retry_after)
            logger.warning(
                "Rate limited (attempt %d/%d). Waiting %.1fs before retry...",
                attempt + 1, self.max_retries, wait,
            )
            print(f"  ⏳ Rate limited (429). Retry {attempt + 1}/{self.max_retries} in {wait:.1f}s...")
            time.sleep(wait)
            last_result = result
        return last_result


# Singletons for use across proxy
_backoff = RateLimitBackoff()
_backoff_sync = RateLimitBackoffSync()


def get_backoff() -> RateLimitBackoff:
    return _backoff


def get_backoff_sync() -> RateLimitBackoffSync:
    return _backoff_sync
