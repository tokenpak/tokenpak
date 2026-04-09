"""tokenpak.proxy.adapters.embedding_router — Failover chain with cooldown for embedding providers.

Routes POST /v1/embeddings to the best available embedding provider, with
automatic failover and per-provider health tracking.

Environment:
    TOKENPAK_EMBEDDING_PROVIDERS        — comma-separated ordered provider list
                                          (default "voyage,openai,cohere")
    TOKENPAK_EMBEDDING_KEY_COOLDOWN     — seconds to cool down a provider after
                                          401/403 (default 300)
    TOKENPAK_EMBEDDING_RETRY_429        — fallback cooldown seconds for 429 when
                                          no Retry-After header present (default 60)
    TOKENPAK_EMBEDDING_VOYAGE_URL       — upstream URL for voyage provider
    TOKENPAK_EMBEDDING_OPENAI_URL       — upstream URL for openai provider
    TOKENPAK_EMBEDDING_COHERE_URL       — upstream URL for cohere provider
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment defaults
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDERS = os.environ.get(
    "TOKENPAK_EMBEDDING_PROVIDERS", "voyage,openai,cohere"
)
_DEFAULT_KEY_COOLDOWN = int(
    os.environ.get("TOKENPAK_EMBEDDING_KEY_COOLDOWN", "300")
)
_DEFAULT_RETRY_429 = int(
    os.environ.get("TOKENPAK_EMBEDDING_RETRY_429", "60")
)

_PROVIDER_UPSTREAM_DEFAULTS: Dict[str, str] = {
    "voyage": "https://api.voyageai.com",
    "openai": "https://api.openai.com",
    "cohere": "https://api.cohere.com",
}


# ---------------------------------------------------------------------------
# ProviderHealth — per-provider cooldown + error tracking
# ---------------------------------------------------------------------------


@dataclass
class ProviderHealthEntry:
    """Health state for a single provider."""

    cooldown_until: float = 0.0  # epoch seconds; 0 = healthy
    error_count: int = 0
    last_error_code: Optional[int] = None
    last_error_time: float = 0.0


class ProviderHealth:
    """Tracks cooldown state and error counts for a set of embedding providers.

    Args:
        cooldown_401: Seconds to mark a provider unhealthy after 401/403.
        cooldown_429: Fallback seconds for 429 when no Retry-After header.
    """

    def __init__(
        self,
        cooldown_401: int = _DEFAULT_KEY_COOLDOWN,
        cooldown_429: int = _DEFAULT_RETRY_429,
    ) -> None:
        self._cooldown_401 = cooldown_401
        self._cooldown_429 = cooldown_429
        self._state: Dict[str, ProviderHealthEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_healthy(self, provider: str) -> bool:
        """Return True if the provider is not in a cooldown period."""
        entry = self._state.get(provider)
        if entry is None:
            return True
        return time.time() >= entry.cooldown_until

    def record_failure(
        self,
        provider: str,
        status_code: int,
        headers: Dict[str, str],
    ) -> None:
        """Update health state based on an upstream failure response.

        Args:
            provider:    Provider name (e.g. "voyage").
            status_code: HTTP status code returned by the provider.
            headers:     Response headers (used to read Retry-After for 429).
        """
        entry = self._state.setdefault(provider, ProviderHealthEntry())
        entry.error_count += 1
        entry.last_error_code = status_code
        entry.last_error_time = time.time()

        if status_code in (401, 403):
            entry.cooldown_until = time.time() + self._cooldown_401
            logger.warning(
                "embedding_router: provider=%s status=%d → cooldown for %ds",
                provider,
                status_code,
                self._cooldown_401,
            )
        elif status_code == 429:
            retry_after = self._parse_retry_after(headers)
            cooldown = retry_after if retry_after else self._cooldown_429
            entry.cooldown_until = time.time() + cooldown
            logger.warning(
                "embedding_router: provider=%s status=429 → cooldown for %ds (Retry-After=%s)",
                provider,
                cooldown,
                retry_after,
            )
        # 5xx and network errors do not trigger a cooldown — caller handles retry

    def record_network_error(self, provider: str) -> None:
        """Record a network-level failure (no HTTP status)."""
        entry = self._state.setdefault(provider, ProviderHealthEntry())
        entry.error_count += 1
        entry.last_error_code = None
        entry.last_error_time = time.time()
        logger.warning("embedding_router: provider=%s network error", provider)

    def get_health_status(self) -> Dict[str, Dict[str, Any]]:
        """Return a snapshot of all provider health states.

        Returns a dict keyed by provider name with fields:
            healthy (bool), cooldown_until (float), error_count (int),
            last_error_code (int|None), seconds_remaining (float).
        """
        now = time.time()
        result: Dict[str, Dict[str, Any]] = {}
        for provider, entry in self._state.items():
            remaining = max(0.0, entry.cooldown_until - now)
            result[provider] = {
                "healthy": now >= entry.cooldown_until,
                "cooldown_until": entry.cooldown_until,
                "seconds_remaining": remaining,
                "error_count": entry.error_count,
                "last_error_code": entry.last_error_code,
                "last_error_time": entry.last_error_time,
            }
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_retry_after(headers: Dict[str, str]) -> Optional[int]:
        """Parse Retry-After header value as seconds (integer only, not date)."""
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is None:
            return None
        try:
            val = int(raw.strip())
            return max(1, val)
        except (ValueError, AttributeError):
            return None


# ---------------------------------------------------------------------------
# EmbeddingRouter — provider chain with failover
# ---------------------------------------------------------------------------

# Type alias for the per-provider call function injected by callers.
# Signature: (provider, upstream_url, request_body) -> (status_code, response_headers, response_body)
ProviderCallFn = Callable[
    [str, str, bytes],
    Tuple[int, Dict[str, str], bytes],
]


class EmbeddingRouter:
    """Routes embedding requests through a failover chain.

    Providers are tried in order.  On failure:
    - 401/403 → provider goes into cooldown; try next.
    - 429     → provider goes into cooldown (respects Retry-After); try next.
    - 5xx     → retry the same provider once, then fall through to next.
    - Network error → fall through immediately.

    Args:
        providers:      Ordered list of provider names to try.
        health:         ProviderHealth instance (shared or per-router).
        upstream_urls:  Mapping of provider → upstream base URL.
        call_provider:  Callable that performs the actual HTTP call.
                        Must return (status_code, headers, body).
    """

    def __init__(
        self,
        providers: Optional[List[str]] = None,
        health: Optional[ProviderHealth] = None,
        upstream_urls: Optional[Dict[str, str]] = None,
        call_provider: Optional[ProviderCallFn] = None,
    ) -> None:
        self._providers = providers or [
            p.strip() for p in _DEFAULT_PROVIDERS.split(",") if p.strip()
        ]
        self._health = health or ProviderHealth()
        self._upstream_urls = {
            **_PROVIDER_UPSTREAM_DEFAULTS,
            **(upstream_urls or {}),
        }
        self._call_provider = call_provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_request(self, request_body: bytes) -> Tuple[int, Dict[str, str], bytes]:
        """Route an embedding request through the provider failover chain.

        Args:
            request_body: Raw request body bytes (JSON payload for /v1/embeddings).

        Returns:
            Tuple of (status_code, response_headers, response_body).

        Raises:
            RuntimeError: When no call_provider function has been configured.
        """
        if self._call_provider is None:
            raise RuntimeError(
                "EmbeddingRouter: call_provider not configured — "
                "inject a ProviderCallFn before calling handle_request()"
            )

        healthy_providers = [p for p in self._providers if self._health.is_healthy(p)]

        if not healthy_providers:
            return self._all_cooldown_response()

        tried: List[str] = []

        for provider in healthy_providers:
            upstream = self._upstream_urls.get(provider, "")
            if not upstream:
                logger.warning(
                    "embedding_router: provider=%s has no upstream URL — skipping",
                    provider,
                )
                continue

            # Attempt the call (with one retry for 5xx)
            status, headers, body = self._attempt(provider, upstream, request_body, tried)

            if status is None:
                # Network error — already logged; try next provider
                tried.append(provider)
                continue

            if 200 <= status < 300:
                if tried:
                    logger.info(
                        "embedding_router: success on provider=%s after skipping %s",
                        provider,
                        tried,
                    )
                return status, headers, body

            if status in (401, 403, 429):
                self._health.record_failure(provider, status, headers)
                tried.append(provider)
                logger.info(
                    "embedding_router: falling through from provider=%s status=%d",
                    provider,
                    status,
                )
                continue

            # Any other non-2xx (should not reach here after retry logic)
            tried.append(provider)
            logger.warning(
                "embedding_router: provider=%s returned status=%d — falling through",
                provider,
                status,
            )

        # All providers tried and failed
        return self._exhausted_response(tried)

    def get_health_status(self) -> Dict[str, Any]:
        """Return health state dict for all known providers.

        Providers that have never had a failure are reported as healthy
        with zeroed counters for completeness.
        """
        tracked = self._health.get_health_status()
        result: Dict[str, Any] = {}
        for provider in self._providers:
            if provider in tracked:
                result[provider] = tracked[provider]
            else:
                result[provider] = {
                    "healthy": True,
                    "cooldown_until": 0.0,
                    "seconds_remaining": 0.0,
                    "error_count": 0,
                    "last_error_code": None,
                    "last_error_time": 0.0,
                }
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _attempt(
        self,
        provider: str,
        upstream: str,
        body: bytes,
        previously_tried: List[str],
    ) -> Tuple[Optional[int], Dict[str, str], bytes]:
        """Call a provider, retrying once on 5xx.

        Returns (None, {}, b"") on network error.
        """
        for attempt_num in range(2):  # 0 = first try, 1 = one retry
            try:
                status, headers, resp_body = self._call_provider(  # type: ignore[misc]
                    provider, upstream, body
                )
            except Exception as exc:
                self._health.record_network_error(provider)
                logger.warning(
                    "embedding_router: provider=%s network error (attempt %d): %s",
                    provider,
                    attempt_num + 1,
                    exc,
                )
                return None, {}, b""

            if 500 <= status < 600:
                if attempt_num == 0:
                    logger.warning(
                        "embedding_router: provider=%s status=%d — retrying once",
                        provider,
                        status,
                    )
                    continue  # retry
                # Second attempt also 5xx — fall through
                logger.warning(
                    "embedding_router: provider=%s status=%d on retry — falling through",
                    provider,
                    status,
                )
                return status, headers, resp_body

            return status, headers, resp_body

        # Should not be reached
        return None, {}, b""  # pragma: no cover

    def _all_cooldown_response(self) -> Tuple[int, Dict[str, str], bytes]:
        """503 response when every provider is in cooldown."""
        import json

        health = self._health.get_health_status()
        detail = {
            p: {
                "cooldown_until": health.get(p, {}).get("cooldown_until", 0),
                "seconds_remaining": health.get(p, {}).get("seconds_remaining", 0),
                "last_error_code": health.get(p, {}).get("last_error_code"),
            }
            for p in self._providers
        }
        body = json.dumps(
            {
                "error": {
                    "type": "all_providers_in_cooldown",
                    "message": "All embedding providers are temporarily unavailable.",
                    "providers": detail,
                }
            }
        ).encode()
        logger.error(
            "embedding_router: all providers in cooldown — returning 503. detail=%s",
            detail,
        )
        return 503, {"Content-Type": "application/json"}, body

    def _exhausted_response(self, tried: List[str]) -> Tuple[int, Dict[str, str], bytes]:
        """503 response when all providers have been tried and failed."""
        import json

        body = json.dumps(
            {
                "error": {
                    "type": "all_providers_failed",
                    "message": "All embedding providers failed.",
                    "tried": tried,
                }
            }
        ).encode()
        logger.error(
            "embedding_router: all providers exhausted — returning 503. tried=%s", tried
        )
        return 503, {"Content-Type": "application/json"}, body
