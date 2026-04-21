"""
TokenPak Intelligence Server — API key authentication + rate limiting.

Key concepts
────────────
* All endpoints require the ``X-TokenPak-Key`` header.
* Keys are mapped to a tier: free | pro | team | enterprise.
* Rate limits are enforced per-key using a sliding-window token bucket:
    - free        → 20 req/min  (unregistered demo keys)
    - pro         → 100 req/min
    - team        → 500 req/min
    - enterprise  → unlimited
* On a rate-limit breach the server returns HTTP 429 with
  ``Retry-After`` and ``X-RateLimit-Reset`` headers.
* PII scrubbing: the logging filter removes bearer tokens and
  ``X-TokenPak-Key`` values from log records.

Usage
─────
::

    from tokenpak.intelligence.auth import (
        APIKeyValidator,
        RateLimiter,
        TokenPakAuthMiddleware,
        LicenseTier,
    )

Environment variables (all optional)
──────────────────────────────────────
TOKENPAK_ALLOWED_KEYS   — comma-separated ``key:tier`` pairs used in tests
                          and dev (e.g. ``testkey1:pro,testkey2:enterprise``).
                          In production, override ``APIKeyValidator.lookup``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from enum import Enum
from typing import Callable, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Tiers
# ──────────────────────────────────────────────────────────────


class LicenseTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


# requests per minute, None = unlimited
TIER_RATE_LIMITS: Dict[LicenseTier, Optional[int]] = {
    LicenseTier.FREE: 20,
    LicenseTier.PRO: 100,
    LicenseTier.TEAM: 500,
    LicenseTier.ENTERPRISE: None,
}

# ──────────────────────────────────────────────────────────────
# PII / secret scrubber (logging filter)
# ──────────────────────────────────────────────────────────────

_REDACT_PATTERNS = [
    re.compile(r"(X-TokenPak-Key:\s*)\S+", re.IGNORECASE),
    re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(\"api_key\"\s*:\s*\")[^\"]+", re.IGNORECASE),
    re.compile(r"(token[=:]\s*)\S+", re.IGNORECASE),
]


class PIIScrubFilter(logging.Filter):
    """Remove API keys and bearer tokens from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in _REDACT_PATTERNS:
            msg = pattern.sub(r"\g<1>[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


# Attach scrubber to the intelligence server logger hierarchy
_intel_logger = logging.getLogger("tokenpak.intelligence")
_intel_logger.addFilter(PIIScrubFilter())

# ──────────────────────────────────────────────────────────────
# API key validator
# ──────────────────────────────────────────────────────────────


class APIKeyValidator:
    """
    Maps API keys to tiers.

    Override ``lookup`` to integrate with a real database.
    For local dev/tests, populate ``TOKENPAK_ALLOWED_KEYS``.
    """

    def __init__(self) -> None:
        self._keys: Dict[str, LicenseTier] = {}
        self._load_env_keys()

    def _load_env_keys(self) -> None:
        raw = os.environ.get("TOKENPAK_ALLOWED_KEYS", "")
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            key, tier_str = pair.rsplit(":", 1)
            key = key.strip()
            tier_str = tier_str.strip().lower()
            try:
                self._keys[key] = LicenseTier(tier_str)
            except ValueError:
                logger.warning("Unknown tier '%s' for key — skipping", tier_str)

    def register(self, key: str, tier: LicenseTier) -> None:
        """Register a key programmatically (useful in tests)."""
        self._keys[key] = tier

    def lookup(self, key: str) -> Optional[LicenseTier]:
        """
        Return the tier for *key*, or ``None`` if unknown.

        In production: replace with DB/Redis lookup.
        """
        return self._keys.get(key)

    def validate(self, key: Optional[str]) -> Tuple[bool, Optional[LicenseTier], str]:
        """
        Returns ``(ok, tier, reason)``.
        ``ok`` is False when the key is missing or unrecognised.
        """
        if not key:
            return False, None, "Missing X-TokenPak-Key header"
        tier = self.lookup(key)
        if tier is None:
            return False, None, "Invalid or unknown API key"
        return True, tier, ""


# ──────────────────────────────────────────────────────────────
# Sliding-window rate limiter (in-memory, per-key)
# ──────────────────────────────────────────────────────────────


class RateLimiter:
    """
    Fixed-window (per-minute) rate limiter.

    Thread-safe; resets at the start of each UTC minute.
    Stores ``(count, window_start)`` per hashed key.
    """

    WINDOW_SECONDS = 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key → [count, window_start_ts]
        self._buckets: Dict[str, list] = defaultdict(lambda: [0, 0.0])

    @staticmethod
    def _hash(key: str) -> str:
        """Hash the raw key so it never appears in memory in plain text."""
        return hashlib.sha256(key.encode()).hexdigest()

    def check(self, key: str, tier: LicenseTier) -> Tuple[bool, int, int]:
        """
        Returns ``(allowed, remaining, reset_ts)``.

        * ``remaining`` — requests left in the current window.
        * ``reset_ts``  — UTC epoch when the window resets.
        """
        limit = TIER_RATE_LIMITS.get(tier)
        if limit is None:
            # Enterprise: unlimited
            window_reset = int(time.time()) + self.WINDOW_SECONDS
            return True, 999_999, window_reset

        hk = self._hash(key)
        now = time.time()

        with self._lock:
            bucket = self._buckets[hk]
            count, window_start = bucket

            # New window?
            if now - window_start >= self.WINDOW_SECONDS:
                bucket[0] = 0
                bucket[1] = now
                count = 0
                window_start = now

            window_reset = int(window_start) + self.WINDOW_SECONDS
            remaining = max(0, limit - count)

            if count >= limit:
                return False, 0, window_reset

            bucket[0] += 1
            return True, remaining - 1, window_reset


# ──────────────────────────────────────────────────────────────
# Starlette middleware
# ──────────────────────────────────────────────────────────────

# Paths that bypass auth (health, metrics)
_BYPASS_PATHS = frozenset(["/health", "/metrics", "/"])


class TokenPakAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Injects a unique ``X-Request-ID`` into every request.
    2. Validates ``X-TokenPak-Key``.
    3. Enforces per-tier rate limits.
    4. Attaches ``request.state.tier`` and ``request.state.request_id``.
    5. Sets rate-limit response headers on every reply.
    """

    def __init__(
        self,
        app,
        validator: Optional[APIKeyValidator] = None,
        limiter: Optional[RateLimiter] = None,
    ) -> None:
        super().__init__(app)
        self.validator = validator or APIKeyValidator()
        self.limiter = limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next: Callable):
        # ── 1. Request ID ──────────────────────────────────────
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # ── 2. Bypass health / metrics ─────────────────────────
        if request.url.path in _BYPASS_PATHS:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        # ── 3. Auth ────────────────────────────────────────────
        api_key = request.headers.get("X-TokenPak-Key")
        ok, tier, reason = self.validator.validate(api_key)
        if not ok:
            logger.info("[%s] 401 auth failure — %s", request_id, reason)
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": reason},
                headers={
                    "X-Request-ID": request_id,
                    "WWW-Authenticate": 'ApiKey realm="TokenPak Intelligence"',
                },
            )

        request.state.tier = tier
        request.state.api_key = api_key

        # ── 4. Rate limit ──────────────────────────────────────
        assert api_key is not None, "api_key should not be None after validation"
        assert tier is not None, "tier should not be None after validation"
        allowed, remaining, reset_ts = self.limiter.check(api_key, tier)

        if not allowed:
            retry_after = max(1, reset_ts - int(time.time()))
            logger.info("[%s] 429 rate limit exceeded tier=%s", request_id, tier)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": f"Rate limit exceeded for tier '{tier}'. Retry after {retry_after}s.",
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-RateLimit-Limit": str(TIER_RATE_LIMITS.get(tier, 0)),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                    "Retry-After": str(retry_after),
                },
            )

        # ── 5. Call endpoint ───────────────────────────────────
        response = await call_next(request)

        # ── 6. Attach rate-limit headers ───────────────────────
        limit_value = TIER_RATE_LIMITS.get(tier)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-RateLimit-Limit"] = (
            str(limit_value) if limit_value is not None else "unlimited"
        )
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_ts)

        return response
