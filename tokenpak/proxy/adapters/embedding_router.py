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

import json
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

# Maps provider name → API key environment variable name used to check availability.
_PROVIDER_KEY_ENV: Dict[str, str] = {
    "voyage": "VOYAGE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "cohere": "CO_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "jina": "JINA_API_KEY",
    "ollama": "TOKENPAK_OLLAMA_URL",
}

# Default embedding model per provider.
_PROVIDER_DEFAULT_MODEL: Dict[str, str] = {
    "voyage": "voyage-3.5",
    "openai": "text-embedding-3-small",
    "cohere": "embed-english-v3.0",
    "gemini": "gemini-embedding-001",
    "jina": "jina-embeddings-v3",
    "ollama": "nomic-embed-text",
}

# Supported output dimension counts per provider.
# None means the provider accepts arbitrary dimensions — skip negotiation.
SUPPORTED_DIMENSIONS: Dict[str, Optional[List[int]]] = {
    "voyage": [256, 512, 1024, 2048],
    "openai": [256, 512, 1024, 1536, 3072],
    "gemini": [128, 256, 512, 768, 1024, 2048, 3072],
    "jina": [32, 64, 128, 256, 512, 768, 1024],
    "ollama": [768],  # nomic-embed-text fixed dimensions
    "cohere": None,  # accepts arbitrary dimensions
}

# Cost per 1M tokens in USD.  Mirrors EMBEDDING_COST_PER_1M in anon_metrics.
_COST_PER_1M: Dict[str, float] = {
    "voyage": 0.06,
    "openai": 0.02,
    "jina": 0.02,
    "gemini": 0.0,
    "ollama": 0.0,
    "cohere": 0.1,
}


def _calc_embedding_cost(provider: str, input_tokens: int) -> float:
    """Return estimated USD cost for an embedding request."""
    rate = _COST_PER_1M.get(provider, 0.0)
    return round(rate * input_tokens / 1_000_000, 8)


def negotiate_dimensions(requested: int, supported: List[int]) -> int:
    """Return the closest supported dimension value to *requested*.

    Ties (equal absolute difference) are broken by selecting the smaller value.

    Args:
        requested: Dimension count requested by the client.
        supported: List of dimension counts supported by the provider.

    Returns:
        The value in *supported* with the smallest absolute difference from
        *requested*.
    """
    return min(supported, key=lambda x: (abs(x - requested), x))


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

    def handle_request(
        self,
        request_body: bytes,
        *,
        cache_hit: bool = False,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Route an embedding request through the provider failover chain.

        Args:
            request_body: Raw request body bytes (JSON payload for /v1/embeddings).
            cache_hit:    True if the caller already served this from a local cache.
                          Used in telemetry and _tokenpak metadata only.

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

        t0 = time.time()
        healthy_providers = [p for p in self._providers if self._health.is_healthy(p)]

        if not healthy_providers:
            status, headers, body = self._all_cooldown_response()
            self._emit_telemetry(
                provider="",
                model="",
                request_body=request_body,
                response_body=body,
                latency_ms=(time.time() - t0) * 1000,
                cache_hit=cache_hit,
                fallback_used=False,
                status=status,
                error="all_providers_in_cooldown",
            )
            return status, headers, body

        tried: List[str] = []

        for provider in healthy_providers:
            upstream = self._upstream_urls.get(provider, "")
            if not upstream:
                logger.warning(
                    "embedding_router: provider=%s has no upstream URL — skipping",
                    provider,
                )
                continue

            # Negotiate dimensions before sending upstream
            normalized_body, requested_dims, actual_dims = self._normalize_request(
                provider, request_body
            )

            # Attempt the call (with one retry for 5xx)
            status, headers, body = self._attempt(provider, upstream, normalized_body, tried)

            if status is None:
                # Network error — already logged; try next provider
                tried.append(provider)
                continue

            if 200 <= status < 300:
                fallback_used = bool(tried)
                if fallback_used:
                    logger.info(
                        "embedding_router: success on provider=%s after skipping %s",
                        provider,
                        tried,
                    )
                latency_ms = (time.time() - t0) * 1000
                body = self._inject_tokenpak(
                    body=body,
                    provider=provider,
                    latency_ms=latency_ms,
                    cached=cache_hit,
                    fallback_used=fallback_used,
                    requested_dims=requested_dims,
                    actual_dims=actual_dims,
                )
                self._emit_telemetry(
                    provider=provider,
                    model="",
                    request_body=request_body,
                    response_body=body,
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    fallback_used=fallback_used,
                    status=status,
                    error=None,
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
        status, headers, body = self._exhausted_response(tried)
        self._emit_telemetry(
            provider="",
            model="",
            request_body=request_body,
            response_body=body,
            latency_ms=(time.time() - t0) * 1000,
            cache_hit=cache_hit,
            fallback_used=bool(tried),
            status=status,
            error="all_providers_failed",
        )
        return status, headers, body

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

    def get_providers_status(self) -> List[Dict]:
        """Return status of all configured providers.

        Each entry contains: name, available, healthy, default_model, key_set, cooldown_until.
        - available/key_set: True if the provider's API key env var is set.
        - healthy: True if the provider is not in a cooldown period.
        - cooldown_until: ISO timestamp if in cooldown, else null.
        """
        health = self.get_health_status()
        result: List[Dict] = []
        for provider in self._providers:
            key_env = _PROVIDER_KEY_ENV.get(provider)
            key_set = bool(os.environ.get(key_env, "").strip()) if key_env else False
            h = health.get(provider, {})
            healthy = h.get("healthy", True)
            cooldown_raw = h.get("cooldown_until", 0.0)
            cooldown_until = (
                None if (not cooldown_raw or cooldown_raw <= time.time())
                else cooldown_raw
            )
            result.append({
                "name": provider,
                "available": key_set,
                "healthy": healthy,
                "default_model": _PROVIDER_DEFAULT_MODEL.get(provider),
                "key_set": key_set,
                "cooldown_until": cooldown_until,
            })
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

    # ------------------------------------------------------------------
    # Dimension negotiation
    # ------------------------------------------------------------------

    def _normalize_request(
        self,
        provider: str,
        body: bytes,
    ) -> Tuple[bytes, Optional[int], Optional[int]]:
        """Adjust the dimensions field in a request body to the closest supported value.

        Returns:
            (normalized_body, requested_dims, actual_dims).
            Both dim values are None when no dimensions field was present or when
            the provider accepts arbitrary dimensions (SUPPORTED_DIMENSIONS entry is None).
            actual_dims == requested_dims means no adjustment was needed.
        """
        supported = SUPPORTED_DIMENSIONS.get(provider)
        if not supported:
            return body, None, None

        try:
            parsed = json.loads(body)
        except Exception:
            return body, None, None

        requested = parsed.get("dimensions")
        if not isinstance(requested, int):
            return body, None, None

        actual = negotiate_dimensions(requested, supported)
        if actual == requested:
            return body, requested, actual

        parsed["dimensions"] = actual
        logger.info(
            "embedding_router: provider=%s dimension adjusted %d → %d",
            provider,
            requested,
            actual,
        )
        return json.dumps(parsed).encode(), requested, actual

    # ------------------------------------------------------------------
    # Telemetry and _tokenpak injection helpers
    # ------------------------------------------------------------------

    def _inject_tokenpak(
        self,
        body: bytes,
        provider: str,
        latency_ms: float,
        cached: bool,
        fallback_used: bool,
        requested_dims: Optional[int] = None,
        actual_dims: Optional[int] = None,
    ) -> bytes:
        """Inject _tokenpak metadata block into a successful JSON response body.

        Parses the body JSON and adds a '_tokenpak' key.  If parsing fails
        the original body is returned unchanged (fail-open).
        """
        try:
            parsed = json.loads(body)
            upstream_model = ""
            if isinstance(parsed, dict):
                # OpenAI-compatible response has 'model' at top level
                upstream_model = parsed.get("model", "")
            tokenpak_meta: Dict[str, Any] = {
                "provider": provider,
                "upstream_model": upstream_model,
                "latency_ms": round(latency_ms, 1),
                "cached": cached,
                "fallback_used": fallback_used,
            }
            if requested_dims is not None and actual_dims is not None and requested_dims != actual_dims:
                tokenpak_meta["dim_adjustment"] = {
                    "requested_dims": requested_dims,
                    "actual_dims": actual_dims,
                }
            parsed["_tokenpak"] = tokenpak_meta
            return json.dumps(parsed).encode()
        except Exception:
            return body  # fail-open: never corrupt a response

    def _emit_telemetry(
        self,
        *,
        provider: str,
        model: str,
        request_body: bytes,
        response_body: bytes,
        latency_ms: float,
        cache_hit: bool,
        fallback_used: bool,
        status: int,
        error: Optional[str],
    ) -> None:
        """Write one embedding telemetry record. Never raises."""
        try:
            # Parse request for input_count
            input_count = 0
            try:
                req = json.loads(request_body)
                inp = req.get("input", [])
                input_count = len(inp) if isinstance(inp, list) else (1 if inp else 0)
            except Exception:
                pass

            # Parse response for model, tokens, dimensions
            upstream_model = model
            input_tokens = 0
            dimensions = 0
            try:
                resp = json.loads(response_body)
                if isinstance(resp, dict):
                    upstream_model = resp.get("model", model) or model
                    usage = resp.get("usage", {})
                    input_tokens = (
                        usage.get("total_tokens")
                        or usage.get("prompt_tokens")
                        or 0
                    )
                    data = resp.get("data", [])
                    if data and isinstance(data, list):
                        emb = data[0].get("embedding", [])
                        dimensions = len(emb) if isinstance(emb, list) else 0
            except Exception:
                pass

            cost_usd = _calc_embedding_cost(provider, input_tokens)

            from tokenpak.telemetry.anon_metrics import record_embedding_request

            record_embedding_request(
                provider=provider,
                model=upstream_model,
                input_count=input_count,
                input_tokens=input_tokens,
                dimensions=dimensions,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                fallback_used=fallback_used,
                cost_usd=cost_usd,
                error=error,
            )
        except Exception:
            pass  # telemetry must never break the proxy

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
