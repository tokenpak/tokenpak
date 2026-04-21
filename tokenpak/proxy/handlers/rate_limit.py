"""
tokenpak.handlers.rate_limit
─────────────────────────────
Exponential backoff helper for rate-limit (429) responses.

Usage::

    backoff = RateLimitBackoff(base_wait=1.0, max_wait=60.0, jitter_factor=0.1)
    wait = backoff.wait_time(attempt)           # exponential growth
    wait = backoff.wait_time(attempt, retry_after=30.0)  # honour Retry-After header
"""

from __future__ import annotations

import math
import random


class RateLimitBackoff:
    """
    Compute wait durations for retrying after a 429 rate-limit response.

    Parameters
    ----------
    base_wait:
        Initial wait in seconds (attempt 0).
    max_wait:
        Hard ceiling on the returned wait time.
    jitter_factor:
        Fraction of the computed wait to add as random jitter.
        0.0 = no jitter (deterministic, good for tests).
        0.1 = ±10 % jitter (default in production use).
    """

    def __init__(
        self,
        base_wait: float = 1.0,
        max_wait: float = 60.0,
        jitter_factor: float = 0.1,
    ) -> None:
        self.base_wait = base_wait
        self.max_wait = max_wait
        self.jitter_factor = jitter_factor

    def wait_time(self, attempt: int, *, retry_after: float | None = None) -> float:
        """
        Return the number of seconds to wait before the next attempt.

        Parameters
        ----------
        attempt:
            Zero-based retry attempt index.  Attempt 0 is the *first* retry
            (i.e. after the initial request already failed).
        retry_after:
            If the server provided a ``Retry-After`` value (in seconds), it is
            returned directly (capped at ``max_wait``).

        Returns
        -------
        float
            Seconds to sleep, in ``[0, max_wait]``.
        """
        if retry_after is not None:
            return min(float(retry_after), self.max_wait)

        # Exponential: base_wait * 2^attempt
        computed = self.base_wait * math.pow(2, attempt)

        if self.jitter_factor:
            jitter = computed * self.jitter_factor * random.random()
            computed += jitter

        return min(computed, self.max_wait)
