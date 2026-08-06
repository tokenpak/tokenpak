"""
TokenPak Provider Router

Routes requests to appropriate LLM providers (Anthropic, OpenAI, Google).
Handles provider detection, cost estimation, and URL construction.
"""

import json
from dataclasses import dataclass
from typing import Collection, Dict, Mapping, Optional, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

# Provider base URLs
PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    # openai-codex: ChatGPT subscription OAuth.  Clients address the standard
    # /v1/responses surface; TokenPak rewrites it to the ChatGPT backend path.
    "openai-codex": "https://chatgpt.com/backend-api",
    "google": "https://generativelanguage.googleapis.com",
}

# Hosts we intercept for logging/processing.
# Custom providers registered in config.yaml are added at startup by config.py.
INTERCEPT_HOSTS = {
    "api.anthropic.com",
    "api.openai.com",
    "chatgpt.com",
    "generativelanguage.googleapis.com",
}

_DEFAULT_ENDPOINT_PORTS = {"http": 80, "https": 443}
_SENSITIVE_ENDPOINT_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "key",
        "token",
    }
)


def _normalize_endpoint_host(hostname: str) -> str:
    """Normalize a DNS/IP host for endpoint identity comparisons."""
    normalized = hostname.lower().rstrip(".")
    if not normalized:
        return ""
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _endpoint_parts(value: str) -> tuple[str, str, int] | None:
    """Return ``(scheme, host, effective_port)`` for a safe HTTP endpoint.

    Userinfo and fragments are intentionally excluded from endpoint identity:
    accepting either would make credentials/log-only material part of routing.
    """
    candidate = str(value).strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        hostname = _normalize_endpoint_host(parsed.hostname or "")
        if (
            scheme not in _DEFAULT_ENDPOINT_PORTS
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or "#" in candidate
        ):
            return None
        port = parsed.port or _DEFAULT_ENDPOINT_PORTS[scheme]
    except (TypeError, ValueError):
        return None
    return scheme, hostname, port


def _endpoint_identity(value: str) -> str:
    """Return normalized ``scheme://host:effective-port`` or ``""``."""
    parts = _endpoint_parts(value)
    if parts is None:
        return ""
    scheme, hostname, port = parts
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{rendered_host}:{port}"


def _normalize_configured_endpoint(value: str) -> tuple[str, str]:
    """Validate and canonicalize a configured custom-provider endpoint.

    The returned pair is ``(canonical_url, endpoint_identity)``. Query
    parameters are allowed for fixed gateway routing, but credential-shaped
    parameters are rejected because custom credentials belong in
    ``api_key_env`` and headers, not URLs that can enter logs.
    """
    candidate = str(value).strip()
    parts = _endpoint_parts(candidate)
    if parts is None:
        raise ValueError(
            "endpoint must be an absolute http(s) URL without userinfo, fragments, or whitespace"
        )
    parsed = urlsplit(candidate)
    scheme, hostname, effective_port = parts
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in _SENSITIVE_ENDPOINT_QUERY_KEYS:
            raise ValueError(
                f"endpoint query parameter {key!r} looks like a credential; use api_key_env"
            )

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    explicit_port = parsed.port
    netloc = rendered_host
    if explicit_port is not None and explicit_port != _DEFAULT_ENDPOINT_PORTS[scheme]:
        netloc = f"{rendered_host}:{explicit_port}"
    path = parsed.path.rstrip("/")
    canonical = urlunsplit((scheme, netloc, path, parsed.query, ""))
    identity = f"{scheme}://{rendered_host}:{effective_port}"
    return canonical, identity


def _host_header_parts(value: str) -> tuple[str, int | None] | None:
    """Return normalized host and optional explicit port from a Host value."""
    candidate = str(value).strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None
    try:
        parsed = urlsplit(f"//{candidate}")
        hostname = _normalize_endpoint_host(parsed.hostname or "")
        if not hostname or parsed.username is not None or parsed.password is not None:
            return None
        return hostname, parsed.port
    except (TypeError, ValueError):
        return None


def _is_codex_oauth_authorization(headers: Dict[str, str]) -> bool:
    """Return true for a supplied OAuth bearer rather than an OpenAI API key.

    The token value is used only for in-memory route classification and is
    never copied into a route result or log record.
    """
    lower_headers = {key.lower(): value for key, value in headers.items()}
    authorization = lower_headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        return False
    credential = authorization[7:].strip()
    return bool(credential) and not credential.lower().startswith("sk-")


def normalize_hostname(value: str) -> str:
    """Return a lower-case hostname from a URL or Host-header value.

    Ports, userinfo, IPv6 brackets, and a terminal DNS dot are normalized by
    the parser.  Invalid or hostname-free values return an empty string.
    """
    candidate = str(value).strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(
            candidate if "://" in candidate or candidate.startswith("//") else f"//{candidate}"
        )
        return _normalize_endpoint_host(parsed.hostname or "")
    except (TypeError, ValueError):
        return ""


def request_hostname(path: str, headers: Mapping[str, str]) -> str:
    """Resolve the destination hostname from absolute-form path or Host."""
    if "://" in path or path.startswith("//"):
        hostname = normalize_hostname(path)
        if hostname:
            return hostname
    for key, value in headers.items():
        if str(key).lower() == "host":
            return normalize_hostname(str(value))
    return ""


def should_intercept(url: str, hosts: Optional[Collection[str]] = None) -> bool:
    """Return True only for an exact hostname in *hosts* or the live registry."""
    hostname = normalize_hostname(url)
    intercept_hosts = INTERCEPT_HOSTS if hosts is None else hosts
    return bool(hostname) and hostname in {normalize_hostname(host) for host in intercept_hosts}


def _join_upstream_url(base_url: str, request_path: str) -> str:
    """Join a configured API base and reverse-proxy request path once.

    Fixed base-query fields win on conflicts; non-conflicting request fields
    are retained. Fragments are rejected because they are client-side URL
    material and must never be forwarded to an HTTP upstream.
    """
    base = urlsplit(base_url)
    request = urlsplit(request_path)
    if request.fragment:
        raise ValueError("request fragments are not valid upstream HTTP targets")
    base_path = base.path.rstrip("/")
    incoming_path = request.path or "/"

    if not base_path:
        joined_path = incoming_path
    else:
        # Custom endpoints commonly end in /v1 while clients send a path that
        # also begins /v1.  Preserve any earlier gateway prefix but do not
        # duplicate the shared terminal API segment.
        terminal = "/" + base_path.rsplit("/", 1)[-1]
        if incoming_path == terminal or incoming_path.startswith(terminal + "/"):
            joined_path = base_path + incoming_path[len(terminal) :]
        elif incoming_path == base_path or incoming_path.startswith(base_path + "/"):
            joined_path = incoming_path
        else:
            joined_path = f"{base_path}/{incoming_path.lstrip('/')}"

    base_query = parse_qsl(base.query, keep_blank_values=True)
    request_query = parse_qsl(request.query, keep_blank_values=True)
    fixed_keys = {key for key, _value in base_query}
    merged_query = base_query + [pair for pair in request_query if pair[0] not in fixed_keys]
    return urlunsplit(
        (base.scheme, base.netloc, joined_path, urlencode(merged_query, doseq=True), "")
    )


# Default costs for unknown models (used as fallback if registry unavailable)
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

    def __init__(
        self,
        custom_urls: Optional[Dict[str, str]] = None,
        custom_hosts: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize router with optional custom provider URLs.

        Args:
            custom_urls: Override default provider URLs (e.g., for proxies)
        """
        self.provider_urls = {**PROVIDER_URLS}
        if custom_urls:
            self.provider_urls.update(custom_urls)
        self.custom_endpoints: dict[str, str] = {}
        authority_candidates: dict[tuple[str, int], set[str]] = {}
        host_candidates: dict[str, set[str]] = {}
        for raw_endpoint, provider in (custom_hosts or {}).items():
            if provider not in self.provider_urls:
                continue
            identity = _endpoint_identity(raw_endpoint)
            if not identity:
                # Backward-compatible constructor input: callers historically
                # supplied a bare hostname. Bind it to that provider's own
                # configured base URL so the route still gains scheme/port
                # identity instead of falling back to hostname-only matching.
                base_identity = _endpoint_identity(self.provider_urls[provider])
                if normalize_hostname(raw_endpoint) != normalize_hostname(
                    self.provider_urls[provider]
                ):
                    continue
                identity = base_identity
            parts = _endpoint_parts(identity)
            if not identity or parts is None:
                continue
            _scheme, hostname, port = parts
            self.custom_endpoints[identity] = provider
            authority_candidates.setdefault((hostname, port), set()).add(provider)
            host_candidates.setdefault(hostname, set()).add(provider)

        self._custom_authorities = {
            authority: next(iter(providers))
            for authority, providers in authority_candidates.items()
            if len(providers) == 1
        }
        self.custom_hosts = {
            hostname: next(iter(providers))
            for hostname, providers in host_candidates.items()
            if len(providers) == 1
        }

    def _custom_provider_for_request(self, path: str, headers: Mapping[str, str]) -> str | None:
        """Resolve a custom route without collapsing distinct endpoint ports."""
        if "://" in path:
            return self.custom_endpoints.get(_endpoint_identity(path))

        host_value = next(
            (str(value) for key, value in headers.items() if str(key).lower() == "host"),
            "",
        )
        host_parts = _host_header_parts(host_value)
        if host_parts is None:
            return None
        hostname, explicit_port = host_parts
        if explicit_port is not None:
            return self._custom_authorities.get((hostname, explicit_port))
        return self.custom_hosts.get(hostname)

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

        Raises:
            ValueError: If Content-Length header doesn't match actual body size.
        """
        # Validate Content-Length before doing any routing work — only when body
        # is actually provided to this call (body=None means caller omitted it
        # for routing purposes; skip validation in that case).
        lower_headers = {k.lower(): v for k, v in headers.items()}
        content_length_str = lower_headers.get("content-length")
        if content_length_str is not None and body is not None:
            try:
                declared = int(content_length_str)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid Content-Length header value: {content_length_str!r}")
            actual = len(body)
            if declared != actual:
                raise ValueError(
                    f"Content-Length mismatch: header says {declared}, body is {actual} bytes"
                )

        # Check if it's already a full URL
        if path.startswith("http"):
            parsed = urlparse(path)
            provider = self._custom_provider_for_request(
                path, headers
            ) or self._detect_provider_from_host(parsed.netloc)
            model = self._extract_model(body) if body else "unknown"
            from .oauth import analyze_request as _analyze_oauth

            _oauth_ctx = _analyze_oauth(parsed.path, headers, model)
            return RouteResult(
                provider=provider,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                full_url=path,
                should_intercept=should_intercept(path),
                model=model,
                auth_type=_oauth_ctx.auth_type,
                is_codex=_oauth_ctx.is_codex,
                skip_cache_keying=_oauth_ctx.skip_cache_keying,
            )

        # Reverse proxy mode - determine provider from headers/path
        provider = self._custom_provider_for_request(path, headers) or self._detect_provider(
            path, headers, body
        )
        base_url = self.provider_urls.get(provider, self.provider_urls["anthropic"])
        model = self._extract_model(body) if body else "unknown"
        from .oauth import analyze_request as _analyze_oauth

        _oauth_ctx = _analyze_oauth(path, headers, model)
        upstream_path = path
        if provider == "openai-codex" and path.split("?", 1)[0] == "/v1/responses":
            suffix = "?" + path.split("?", 1)[1] if "?" in path else ""
            upstream_path = "/codex/responses" + suffix
        full_url = (
            _join_upstream_url(base_url, upstream_path)
            if provider.startswith("custom-")
            else base_url + upstream_path
        )
        return RouteResult(
            provider=provider,
            base_url=base_url,
            full_url=full_url,
            should_intercept=True,  # Reverse proxy always intercepts
            model=model,
            auth_type=_oauth_ctx.auth_type,
            is_codex=_oauth_ctx.is_codex,
            skip_cache_keying=_oauth_ctx.skip_cache_keying,
        )

    def _detect_provider_from_host(self, host: str) -> str:
        """Detect provider from hostname."""
        host_lower = normalize_hostname(host)
        if host_lower == "anthropic.com" or host_lower.endswith(".anthropic.com"):
            return "anthropic"
        elif host_lower == "chatgpt.com" or host_lower.endswith(".chatgpt.com"):
            return "openai-codex"
        elif host_lower == "openai.com" or host_lower.endswith(".openai.com"):
            return "openai"
        elif (
            host_lower == "googleapis.com"
            or host_lower.endswith(".googleapis.com")
            or host_lower == "google.com"
            or host_lower.endswith(".google.com")
        ):
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
        if "/codex/responses" in path or "/v1/codex/responses" in path:
            # ChatGPT Codex backend path (OpenClaw's tokenpak-openai-codex
            # provider posts here). Proxy injects the JWT from
            # ~/.codex/auth.json and rewrites to chatgpt.com/backend-api.
            return "openai-codex"
        if "/v1/responses" in path:
            # The same Responses path is used by both OpenAI API-key clients
            # and Codex subscription clients.  Preserve API-key traffic on
            # api.openai.com; only an OAuth bearer selects the ChatGPT backend.
            return "openai-codex" if _is_codex_oauth_authorization(headers) else "openai"
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
            return cast(str, data.get("model", "unknown"))
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
    # Get costs from dynamic registry
    from tokenpak.models import get_model_costs

    costs = get_model_costs(model) if model else DEFAULT_COSTS

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

from tokenpak.vault.search import (  # noqa: E402
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
