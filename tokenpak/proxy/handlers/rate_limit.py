"""
tokenpak/handlers/rate_limit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Exponential backoff handler for 429 / rate-limit responses.

Supports:
- Exponential backoff with configurable base_wait and multiplier (2^attempt)
- max_wait ceiling
- Retry-After header override (capped at max_wait)
- Optional jitter via jitter_factor (additive, stays ≤ max_wait)
- Deterministic mode when jitter_factor=0
"""

from __future__ import annotations

import random


class RateLimitBackoff:
    """Exponential backoff calculator for rate-limited API requests.

    Parameters
    ----------
    base_wait : float
        Base wait time in seconds for attempt 0. Default: 1.0.
    max_wait : float
        Maximum wait time ceiling in seconds. Default: 60.0.
    jitter_factor : float
        Fraction of computed wait to add as random jitter (0 = deterministic).
        Jitter is drawn from [0, jitter_factor * computed_wait). Default: 0.1.
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

    def wait_time(self, attempt: int, retry_after: float | None = None) -> float:
        """Calculate the wait time for a given attempt number.

        Parameters
        ----------
        attempt : int
            Zero-indexed retry attempt number. Negative values yield sub-base waits.
        retry_after : float | None
            If provided (e.g. from a ``Retry-After`` header), use this value
            directly instead of computing exponential backoff. Still capped at
            ``max_wait``.

        Returns
        -------
        float
            Seconds to wait before the next retry.
        """
        if retry_after is not None:
            return min(retry_after, self.max_wait)

        # Exponential: base_wait * 2^attempt
        computed = self.base_wait * (2.0 ** attempt)

        # Apply jitter (additive, uniform in [0, jitter_factor * computed))
        if self.jitter_factor > 0:
            computed += random.random() * self.jitter_factor * computed

        # Cap at max_wait
        return min(computed, self.max_wait)
