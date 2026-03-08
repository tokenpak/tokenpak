"""
Rate limit backoff handler for TokenPak proxy.
Implements exponential backoff with jitter to handle HTTP 429 responses.
"""
import asyncio
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimitBackoff:
    """Exponential backoff with jitter for 429 responses."""

    def __init__(self, max_retries: int = 4, base_wait: float = 1.0, max_wait: float = 60.0, jitter_factor: float = 0.2):
        self.max_retries = max_retries
        self.base_wait = base_wait
        self.max_wait = max_wait
        self.jitter_factor = jitter_factor

    def wait_time(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate wait time for given attempt (0-indexed)."""
        if retry_after:
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
                    logger.warning(f"Rate limited (attempt {attempt + 1}/{self.max_retries}). Waiting {wait:.1f}s...")
                    await asyncio.sleep(wait)
                    last_exc = e
                else:
                    raise
        raise last_exc


_backoff = RateLimitBackoff()


def get_backoff() -> RateLimitBackoff:
    return _backoff
