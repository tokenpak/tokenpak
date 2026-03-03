"""
TokenPak Provider Router

Routes requests to appropriate LLM providers (Anthropic, OpenAI, Google).
Handles provider detection, cost estimation, and URL construction.
"""

import json
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, ParseResult


# Provider base URLs
PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
}

# Hosts we intercept for logging/processing
INTERCEPT_HOSTS = {"api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com"}


# Model cost per million tokens (input/output)
MODEL_COSTS = {
    # Anthropic models
    "claude-opus-4-5":    {"input": 15.0,  "output": 75.0},
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-5":  {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0},
    "claude-haiku-3-5":   {"input": 0.8,   "output": 4.0},
    "claude-haiku-4-5":   {"input": 0.8,   "output": 4.0},
    # OpenAI models
    "gpt-4o":             {"input": 5.0,   "output": 15.0},
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.6},
    "gpt-4-turbo":        {"input": 10.0,  "output": 30.0},
    "gpt-4":              {"input": 30.0,  "output": 60.0},
    "gpt-3.5-turbo":      {"input": 0.5,   "output": 1.5},
    # Google models
    "gemini-pro":         {"input": 0.5,   "output": 1.5},
    "gemini-1.5-pro":     {"input": 3.5,   "output": 10.5},
}

# Default costs for unknown models
DEFAULT_COSTS = {"input": 3.0, "output": 15.0}


@dataclass
class RouteResult:
    """Result of routing a request."""
    provider: str  # "anthropic", "openai", "google", "unknown"
    base_url: str
    full_url: str
    should_intercept: bool  # Whether to apply compression/logging
    model: str


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
            return RouteResult(
                provider=provider,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                full_url=path,
                should_intercept=parsed.netloc in INTERCEPT_HOSTS,
                model=self._extract_model(body) if body else "unknown",
            )
        
        # Reverse proxy mode - determine provider from headers/path
        provider = self._detect_provider(path, headers, body)
        base_url = self.provider_urls.get(provider, self.provider_urls["anthropic"])
        
        return RouteResult(
            provider=provider,
            base_url=base_url,
            full_url=base_url + path,
            should_intercept=True,  # Reverse proxy always intercepts
            model=self._extract_model(body) if body else "unknown",
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
        """Detect provider from path, headers, and body."""
        # Path-based detection
        if "/v1/messages" in path:
            return "anthropic"
        if "/chat/completions" in path:
            return "openai"
        if "/models/" in path and "generateContent" in path:
            return "google"
        
        # Header-based detection
        if headers.get("x-api-key") or headers.get("anthropic-version"):
            return "anthropic"
        if headers.get("Authorization", "").startswith("Bearer "):
            # Could be either OpenAI or Google, check path
            if "google" in path.lower():
                return "google"
            return "openai"
        
        # Body-based detection (model name patterns)
        if body:
            model = self._extract_model(body)
            if model.startswith("claude"):
                return "anthropic"
            if model.startswith("gpt"):
                return "openai"
            if model.startswith("gemini"):
                return "google"
        
        # Default to Anthropic (most common use case)
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
    
    if any(x in model_lower for x in ["opus", "gpt-4-turbo", "gpt-4"]) and "mini" not in model_lower:
        return "premium"
    elif any(x in model_lower for x in ["sonnet", "gpt-4o", "gemini-1.5-pro"]) and "mini" not in model_lower:
        return "standard"
    elif any(x in model_lower for x in ["haiku", "mini", "gpt-3.5", "gemini-pro"]):
        return "economy"
    
    return "unknown"
