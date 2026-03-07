"""
TokenPak Provider Router

Routes requests to appropriate LLM providers (Anthropic, OpenAI, Google).
Handles provider detection, cost estimation, and URL construction.
"""

import json
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlparse

# Provider base URLs
PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    # openai-codex: subscription OAuth model (gpt-5.x-codex series)
    # Uses api.openai.com with OAuth Bearer token instead of API key.
    # Preferred endpoint: /v1/responses (Responses API)
    "openai-codex": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
}

# Hosts we intercept for logging/processing
INTERCEPT_HOSTS = {"api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com"}


# Model cost per million tokens (input/output)
MODEL_COSTS = {
    # Anthropic models
    "claude-opus-4-5": {"input": 15.0, "output": 75.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-3-5": {"input": 0.8, "output": 4.0},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0},
    # OpenAI models
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
    # Google models
    "gemini-pro": {"input": 0.5, "output": 1.5},
    "gemini-1.5-pro": {"input": 3.5, "output": 10.5},
    # OpenAI Codex subscription models (gpt-5.x-codex series, OAuth-only)
    "gpt-5.1-codex-mini": {"input": 1.5, "output": 6.0},
    "gpt-5.2-codex": {"input": 3.0, "output": 12.0},
    "gpt-5.3-codex": {"input": 3.0, "output": 12.0},
    "gpt-5.3-codex-spark": {"input": 1.5, "output": 6.0},
}

# Default costs for unknown models
DEFAULT_COSTS = {"input": 3.0, "output": 15.0}


@dataclass
class RouteResult:
    """Result of routing a request."""

    provider: str  # "anthropic", "openai", "openai-codex", "google", "unknown"
    base_url: str
    full_url: str
    should_intercept: bool  # Whether to apply compression/logging
    model: str
    auth_type: str = "apikey"  # "apikey" | "oauth" | "none"
    is_codex: bool = False  # True for Codex subscription models
    skip_cache_keying: bool = False  # True for OAuth (token may expire)


class ProviderRouter:
    """
    Routes requests to appropriate LLM providers.

    Detection priority:
    1. Explicit path patterns (/v1/messages → Anthropic, /v1/chat/completions → OpenAI)
    2. Header presence (x-api-key → Anthropic, Bearer → OpenAI)
    3. Request body model field
    """

    def __init__(self, custom_urls: Optional[Dict[str, str]] = None):
        """
        Initialize router with optional custom provider URLs.

        Args:
            custom_urls: Override default provider URLs (e.g., for proxies)
        """
        self.provider_urls = {**PROVIDER_URLS}
        if custom_urls:
            self.provider_urls.update(custom_urls)

    def route(
        self,
        path: str,
        headers: Dict[str, str],
        body: Optional[bytes] = None,
    ) -> RouteResult:
        """
        Route a request to the appropriate provider.

        Args:
            path: Request path (may be full URL or just path)
            headers: Request headers
            body: Optional request body (for model detection)

        Returns:
            RouteResult with provider info and full URL
        """
        # Check if it's already a full URL
        if path.startswith("http"):
            parsed = urlparse(path)
            provider = self._detect_provider_from_host(parsed.netloc)
            model = self._extract_model(body) if body else "unknown"
            from .oauth import analyze_request as _analyze_oauth

            _oauth_ctx = _analyze_oauth(parsed.path, headers, model)
            return RouteResult(
                provider=provider,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                full_url=path,
                should_intercept=parsed.netloc in INTERCEPT_HOSTS,
                model=model,
                auth_type=_oauth_ctx.auth_type,
                is_codex=_oauth_ctx.is_codex,
                skip_cache_keying=_oauth_ctx.skip_cache_keying,
            )

        # Reverse proxy mode - determine provider from headers/path
        provider = self._detect_provider(path, headers, body)
        base_url = self.provider_urls.get(provider, self.provider_urls["anthropic"])
        model = self._extract_model(body) if body else "unknown"
        from .oauth import analyze_request as _analyze_oauth

        _oauth_ctx = _analyze_oauth(path, headers, model)
        return RouteResult(
            provider=provider,
            base_url=base_url,
            full_url=base_url + path,
            should_intercept=True,  # Reverse proxy always intercepts
            model=model,
            auth_type=_oauth_ctx.auth_type,
            is_codex=_oauth_ctx.is_codex,
            skip_cache_keying=_oauth_ctx.skip_cache_keying,
        )

    def _detect_provider_from_host(self, host: str) -> str:
        """Detect provider from hostname."""
        host_lower = host.lower()
        if "anthropic" in host_lower:
            return "anthropic"
        elif "openai" in host_lower:
            return "openai"
        elif "googleapis" in host_lower or "google" in host_lower:
            return "google"
        return "unknown"

    def _detect_provider(
        self,
        path: str,
        headers: Dict[str, str],
        body: Optional[bytes] = None,
    ) -> str:
        """Detect provider from path, headers, and body.

        Detection priority:
        1. Path patterns (/v1/messages → anthropic, /v1/responses → openai-codex)
        2. Anthropic-specific headers (x-api-key, anthropic-version)
        3. Body model name (claude → anthropic, codex → openai-codex, gpt → openai)
        4. Bearer token presence (non-Google Bearer → openai)
        5. Default: anthropic
        """
        # Path-based detection (highest priority)
        if "/v1/messages" in path:
            return "anthropic"
        if "/v1/responses" in path:
            # OpenAI Responses API — used by Codex subscription models
            return "openai-codex"
        if "/chat/completions" in path:
            return "openai"
        if "/models/" in path and "generateContent" in path:
            return "google"

        # Anthropic-specific header detection
        lower_headers = {k.lower(): v for k, v in headers.items()}
        if lower_headers.get("x-api-key") or lower_headers.get("anthropic-version"):
            return "anthropic"

        # Body-based detection (model name patterns)
        if body:
            model = self._extract_model(body)
            if model.startswith("claude"):
                return "anthropic"
            if "codex" in model.lower():
                return "openai-codex"
            if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
                return "openai"
            if model.startswith("gemini"):
                return "google"

        # Header-based detection (lower priority than path/body)
        auth = lower_headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            if "google" in path.lower():
                return "google"
            return "openai"

        # Default to Anthropic (most common reverse-proxy use case)
        return "anthropic"

    def _extract_model(self, body: bytes) -> str:
        """Extract model name from request body."""
        try:
            data = json.loads(body)
            return data.get("model", "unknown")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "unknown"


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """
    Estimate cost for a request in dollars.

    Args:
        model: Model name (e.g., "claude-sonnet-4-5")
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cache_read_tokens: Tokens read from cache (90% discount)
        cache_creation_tokens: Tokens written to cache (25% premium)

    Returns:
        Estimated cost in dollars
    """
    # Find matching cost entry
    costs = DEFAULT_COSTS
    model_lower = model.lower()
    for key, cost_entry in MODEL_COSTS.items():
        if key in model_lower:
            costs = cost_entry
            break

    # Calculate regular input (excluding cache tokens)
    regular_input = max(0, input_tokens - cache_read_tokens - cache_creation_tokens)

    # Apply costs
    input_cost = regular_input * costs["input"]
    cache_read_cost = cache_read_tokens * costs["input"] * 0.1  # 90% discount
    cache_creation_cost = cache_creation_tokens * costs["input"] * 1.25  # 25% premium
    output_cost = output_tokens * costs["output"]

    total = (input_cost + cache_read_cost + cache_creation_cost + output_cost) / 1_000_000
    return total


def get_model_tier(model: str) -> str:
    """
    Get the tier (pricing category) for a model.

    Returns: "premium", "standard", "economy", or "unknown"
    """
    model_lower = model.lower()

    if (
        any(x in model_lower for x in ["opus", "gpt-4-turbo", "gpt-4"])
        and "mini" not in model_lower
    ):
        return "premium"
    elif (
        any(x in model_lower for x in ["sonnet", "gpt-4o", "gemini-1.5-pro"])
        and "mini" not in model_lower
    ):
        return "standard"
    elif any(x in model_lower for x in ["haiku", "mini", "gpt-3.5", "gemini-pro"]):
        return "economy"

    return "unknown"


# ---------------------------------------------------------------------------
# Vault retrieval helpers — re-exported here for proxy-layer consumers
# ---------------------------------------------------------------------------
# These functions provide cache-stable BM25 retrieval injection used by the
# proxy to keep prompt structures byte-identical across repeated requests.

from tokenpak.agent.vault.retrieval import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    RETRIEVED_CONTEXT_HEADER,
    inject_retrieved_context,
    measure_injection_consistency,
    sort_retrieval_results,
)

__all__ = [
    "ProviderRouter",
    "RouteResult",
    "estimate_cost",
    "get_model_tier",
    "sort_retrieval_results",
    "inject_retrieved_context",
    "measure_injection_consistency",
    "RETRIEVED_CONTEXT_HEADER",
    "DEFAULT_MAX_TOKENS",
]
