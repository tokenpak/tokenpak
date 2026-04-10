"""
tokenpak.proxy.circuit_breaker — Ollama routing, per-provider circuit breakers,
rate limiting, header sanitizing, and error enrichment helpers.

Also contains the OOP CircuitBreaker / CircuitBreakerRegistry classes
merged from agent.proxy.circuit_breaker (FIN-07).

Extracted from runtime/proxy.py (L812-1146) as part of TPK-RESTRUCTURE-003.
"""
import os
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .config import _cfg

# ---------------------------------------------------------------------------
# Ollama upstream routing
# ---------------------------------------------------------------------------

OLLAMA_UPSTREAM: str = _cfg(
    "upstream.ollama", "http://localhost:11434", "TOKENPAK_OLLAMA_UPSTREAM", str
)
OLLAMA_CONNECT_TIMEOUT: int = _cfg("upstream.ollama_timeout", 20, "TOKENPAK_OLLAMA_TIMEOUT", int)

# Circuit breaker for ollama upstream — avoids repeated 2-min TCP hangs
_ollama_circuit: dict = {
    "open": False,          # True = upstream known-dead, skip attempts
    "last_failure": 0.0,    # timestamp of last failure
    "cooldown": 120,        # seconds before retrying after failure
}
_ollama_circuit_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Per-provider circuit breakers (Anthropic, OpenAI, Google)
# ---------------------------------------------------------------------------

_provider_circuits: dict = {
    "anthropic": {
        "failures": 0,
        "open": False,
        "last_failure": 0.0,
        "threshold": 5,
        "cooldown": 60,
    },
    "openai": {
        "failures": 0,
        "open": False,
        "last_failure": 0.0,
        "threshold": 5,
        "cooldown": 60,
    },
    "google": {
        "failures": 0,
        "open": False,
        "last_failure": 0.0,
        "threshold": 5,
        "cooldown": 60,
    },
}
_provider_circuit_lock = threading.Lock()


def _provider_for_url(url: str) -> str:
    """Return the provider name for a given upstream URL."""
    if "anthropic.com" in url:
        return "anthropic"
    if "openai.com" in url:
        return "openai"
    if "googleapis.com" in url:
        return "google"
    return ""


def _circuit_check(provider: str) -> bool:
    """Return True if circuit is OPEN (requests should be rejected)."""
    if not provider:
        return False
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return False
        if cb["open"]:
            if time.time() - cb["last_failure"] > cb["cooldown"]:
                cb["open"] = False
                cb["failures"] = 0
                print(f"  ✅ Circuit breaker CLOSED for {provider} (cooldown expired)")
                return False
            return True
        return False


def _circuit_record_failure(provider: str) -> None:
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return
        cb["failures"] += 1
        cb["last_failure"] = time.time()
        if cb["failures"] >= cb["threshold"]:
            cb["open"] = True
            print(f"  ⚡ Circuit breaker OPEN for {provider} after {cb['failures']} failures")


def _circuit_record_success(provider: str) -> None:
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if cb:
            cb["failures"] = 0
            cb["open"] = False


# ---------------------------------------------------------------------------
# Per-IP rate limiting — token bucket, 60 req/min per IP by default
# ---------------------------------------------------------------------------

_RATE_LIMIT_RPM: int = _cfg("rate_limit_rpm", 60, "TOKENPAK_RATE_LIMIT_RPM", int)
_rate_buckets: dict = {}
_rate_bucket_lock = threading.Lock()

# Request body size limit — configurable, default 10 MB
_MAX_REQUEST_BYTES: int = int(
    os.environ.get("TOKENPAK_MAX_REQUEST_SIZE", str(10 * 1024 * 1024))
)

# ---------------------------------------------------------------------------
# Headers that must NEVER be forwarded upstream (security / hop-by-hop)
# ---------------------------------------------------------------------------

_BLOCKED_FORWARD_HEADERS: frozenset = frozenset(
    {
        "host",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "content-length",
        "accept-encoding",
        "x-forwarded-for",
        "x-real-ip",
        "x-forwarded-host",     # prevent IP spoofing upstream
        "x-tokenpak-bypass",    # internal header — never forward to upstream
    }
)


def _sanitize_headers(raw_headers) -> dict:
    """Build a clean forwarding header dict, stripping hop-by-hop and dangerous headers."""
    result = {}
    for key in raw_headers:
        if key.lower() in _BLOCKED_FORWARD_HEADERS:
            continue
        result[key] = raw_headers[key]
    return result


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _suggest_model(requested: str) -> Optional[str]:
    """Return the closest known model name for a given (possibly wrong) model string."""
    import sys as _sys

    _mod = _sys.modules[__name__]
    _known = list(getattr(_mod, "MODEL_COSTS", {}).keys())
    if not _known or not requested:
        return None
    req_l = requested.lower()
    # Exact partial match first
    for m in _known:
        if req_l in m or m in req_l:
            return m
    # Fallback: pick by prefix (provider family)
    for prefix in ("claude", "gpt", "gemini"):
        if req_l.startswith(prefix):
            candidates = [m for m in _known if m.startswith(prefix)]
            if candidates:
                return candidates[-1]  # newest in list
    return _known[0] if _known else None


def _make_structured_error(
    error_type: str, message: str, suggestion: str, status: int = 400, **extra
) -> dict:
    """Build a flat, user-facing structured error response.

    Returns a dict of the form::

        {"error": "<type>", "message": "<message>", "suggestion": "<suggestion>", ...extra}

    This is the canonical format for user-facing errors surfaced directly by the proxy
    (not forwarded from upstream).  Upstream errors go through _enrich_upstream_error instead.
    """
    payload: dict = {"error": error_type, "message": message, "suggestion": suggestion}
    payload.update(extra)
    return payload


def _enrich_upstream_error(
    normalized: dict, status: int, retry_after_header: Optional[str] = None
) -> dict:
    """Add actionable ``hint`` / ``suggestion`` and ``retry_after`` fields to a normalized error dict.

    Covers five key error paths:
      1. Invalid API key (401 / authentication_error)
      2. Model not found (404 / model_not_found / not_found_error)
      3. Rate limit exceeded (429 / rate_limit_error)
      4. Malformed request body (400 / validation_error / invalid_request_error)
      5. Provider unavailable (502 / 503 / provider_unavailable)
    """
    err = normalized.get("error", {})
    err_type = err.get("type", "")
    err_msg = err.get("message", "").lower()

    # 1. Invalid API key
    if status == 401 or err_type in ("authentication_error", "auth_error", "invalid_api_key"):
        suggestion = (
            "Your API key was rejected by the upstream provider. "
            "Check that the key is valid and has not expired. "
            "Anthropic keys: https://console.anthropic.com/settings/keys | "
            "OpenAI keys: https://platform.openai.com/api-keys"
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 2. Model not found
    elif (
        status == 404
        or err_type in ("model_not_found", "not_found_error")
        or "model" in err_msg
        and "not found" in err_msg
    ):
        _req_model = err.get("model") or ""
        if not _req_model:
            import re as _re

            _m = _re.search(r"model[:\s]+['\"]?([^\s'\"]+)['\"]?", err.get("message", ""), _re.I)
            _req_model = _m.group(1) if _m else ""
        _suggested = _suggest_model(_req_model) if _req_model else None
        suggestion = "The requested model was not found on the upstream provider."
        if _suggested:
            suggestion += f" Did you mean: '{_suggested}'?"
        suggestion += " Check the model ID in your request matches a supported model."
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 3. Rate limit hit
    elif status == 429 or err_type in ("rate_limit_error", "rate_limit_exceeded"):
        _ra = retry_after_header or err.get("retry_after")
        suggestion = "Provider returned 429 — upstream rate limit exceeded."
        if _ra:
            try:
                err["retry_after"] = int(float(_ra))
                suggestion += f" Retry after {err['retry_after']} seconds."
            except (ValueError, TypeError):
                err["retry_after"] = _ra
        suggestion += (
            " Consider implementing exponential backoff or switching to a backup provider."
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 4. Malformed request (400 / invalid_request_error / invalid_json)
    elif status == 400 or err_type in ("invalid_request_error", "validation_error", "invalid_json"):
        _msg = err.get("message", "")
        if err_type == "invalid_json":
            suggestion = "The request body must be valid JSON. Check for missing quotes, trailing commas, or unescaped characters."
        else:
            suggestion = "The request body is invalid."
            import re as _re

            _fld = None
            if "messages" in _msg.lower():
                _fld = "messages"
                suggestion += " The 'messages' field is required and must be a non-empty array."
            elif "model" in _msg.lower():
                _fld = "model"
                suggestion += " The 'model' field is required and must be a non-empty string."
            else:
                _field_m = _re.search(
                    r"(?:field|param(?:eter)?)\s+['\"]?([a-zA-Z_]\w*)['\"]?", _msg, _re.I
                )
                if _field_m:
                    _fld = _field_m.group(1)
                    suggestion += f" Check the '{_fld}' field in your request."
            if _fld:
                err.setdefault("field", _fld)
        suggestion += " See: https://docs.anthropic.com/en/api/messages"
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 5. Provider unavailable (502 / 503)
    elif status in (502, 503) or err_type in (
        "provider_unavailable",
        "service_unavailable",
        "bad_gateway",
    ):
        suggestion = (
            "The upstream provider is temporarily unavailable. "
            "Retry after a short delay. If the issue persists, check the provider's status page "
            "or switch to an alternate provider."
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)
        if not err.get("type"):
            err["type"] = "provider_unavailable"

    normalized["error"] = err
    return normalized


# ---------------------------------------------------------------------------
# Rate limit check
# ---------------------------------------------------------------------------

def _rate_limit_check(client_ip: str) -> bool:
    """Return True if request is ALLOWED. False = throttle (429)."""
    if _RATE_LIMIT_RPM <= 0:
        return True  # disabled
    now = time.time()
    with _rate_bucket_lock:
        if client_ip not in _rate_buckets:
            _rate_buckets[client_ip] = {"tokens": float(_RATE_LIMIT_RPM), "last_refill": now}
        bucket = _rate_buckets[client_ip]
        elapsed = now - bucket["last_refill"]
        refill = elapsed * (_RATE_LIMIT_RPM / 60.0)
        bucket["tokens"] = min(float(_RATE_LIMIT_RPM), bucket["tokens"] + refill)
        bucket["last_refill"] = now
        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False


# ---------------------------------------------------------------------------
# Ollama health check background thread
# ---------------------------------------------------------------------------

def _ollama_health_loop() -> None:
    """Background thread: ping ollama upstream every 30s.
    Pre-opens circuit if unreachable so requests fail instantly."""
    from urllib.parse import urlparse

    parsed = urlparse(OLLAMA_UPSTREAM)
    host = parsed.hostname
    port = parsed.port or 11434
    check_interval = 30  # seconds between checks

    # Initial check on startup
    time.sleep(0.5)  # let proxy finish starting

    while True:
        try:
            probe = socket.create_connection((host, port), timeout=5)
            probe.close()
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = False
            if was_open:
                print(f"  ✅ Ollama upstream {host}:{port} is back online")
        except (socket.timeout, OSError, ConnectionRefusedError):
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            if not was_open:
                print(f"  ⚠️ Ollama upstream {host}:{port} unreachable — circuit opened")

        time.sleep(check_interval)


# Start health checker thread on module load
_ollama_health_thread = threading.Thread(target=_ollama_health_loop, daemon=True)
_ollama_health_thread.start()


# ===========================================================================
# OOP Circuit Breaker (merged from agent.proxy.circuit_breaker — FIN-07)
# ===========================================================================


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Fast-failing
    HALF_OPEN = "half_open"  # Probing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for all circuit breakers."""

    enabled: bool = True
    failure_threshold: int = 5   # failures in window before tripping
    recovery_timeout: float = 60.0  # seconds before OPEN → HALF_OPEN
    window_seconds: float = 60.0    # rolling failure counting window

    @classmethod
    def from_env(cls) -> "CircuitBreakerConfig":
        return cls(
            enabled=os.environ.get("TOKENPAK_CB_ENABLED", "1") != "0",
            failure_threshold=int(os.environ.get("TOKENPAK_CB_FAILURE_THRESHOLD", "5")),
            recovery_timeout=float(os.environ.get("TOKENPAK_CB_RECOVERY_TIMEOUT", "60")),
            window_seconds=float(os.environ.get("TOKENPAK_CB_WINDOW_SECONDS", "60")),
        )


class CircuitBreaker:
    """
    Thread-safe circuit breaker for a single provider.

    State machine::

        CLOSED  ──(threshold failures in window)──▶  OPEN
        OPEN    ──(recovery_timeout elapsed)──────▶  HALF_OPEN
        HALF_OPEN ─(success)─▶  CLOSED
        HALF_OPEN ─(failure)─▶  OPEN  (timer reset)
    """

    def __init__(self, provider: str, config: CircuitBreakerConfig) -> None:
        self.provider = provider
        self._config = config
        self._lock = threading.Lock()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_times: deque = deque()
        self._opened_at: float = 0.0
        self._probe_in_flight: bool = False
        self._total_trips: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0

    def allow_request(self) -> bool:
        """Return True if the request should proceed, False to fast-fail."""
        if not self._config.enabled:
            return True
        with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                elapsed = now - self._opened_at
                if elapsed >= self._config.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = True
                    return True
                return False
            if self._state == CircuitState.HALF_OPEN:
                if not self._probe_in_flight:
                    self._probe_in_flight = True
                    return True
                return False
        return True

    def record_success(self) -> None:
        """Record a successful response. Resets circuit if in HALF_OPEN."""
        with self._lock:
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_times.clear()
                self._probe_in_flight = False

    def record_failure(self) -> None:
        """Record a failed response. May trip the circuit."""
        with self._lock:
            now = time.monotonic()
            self._total_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._probe_in_flight = False
                return
            if self._state == CircuitState.OPEN:
                return
            self._failure_times.append(now)
            cutoff = now - self._config.window_seconds
            while self._failure_times and self._failure_times[0] < cutoff:
                self._failure_times.popleft()
            if len(self._failure_times) >= self._config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._total_trips += 1

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def status(self) -> Dict[str, Any]:
        """Return a status dict for the /health endpoint."""
        with self._lock:
            now = time.monotonic()
            failures_in_window = len(self._failure_times)
            time_until_probe: Optional[float] = None
            if self._state == CircuitState.OPEN:
                remaining = self._config.recovery_timeout - (now - self._opened_at)
                time_until_probe = max(0.0, round(remaining, 1))
            return {
                "state": self._state.value,
                "failures_in_window": failures_in_window,
                "failure_threshold": self._config.failure_threshold,
                "time_until_probe_seconds": time_until_probe,
                "total_trips": self._total_trips,
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
            }

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_times.clear()
            self._probe_in_flight = False
            self._opened_at = 0.0


# Known provider URL substrings → canonical provider names
_PROVIDER_PATTERNS: List[tuple] = [
    ("anthropic.com", "anthropic"),
    ("openai.com", "openai"),
    ("googleapis.com", "google"),
    ("generativelanguage", "google"),
    ("azure.com", "azure"),
    ("ollama", "ollama"),
    ("groq.com", "groq"),
    ("together.xyz", "together"),
    ("cohere.com", "cohere"),
]


def provider_from_url(url: str) -> str:
    """Infer the canonical provider name from a target URL."""
    url_lower = url.lower()
    for pattern, provider in _PROVIDER_PATTERNS:
        if pattern in url_lower:
            return provider
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


class CircuitBreakerRegistry:
    """Thread-safe registry of per-provider circuit breakers."""

    def __init__(self, config: Optional[CircuitBreakerConfig] = None) -> None:
        self._config = config or CircuitBreakerConfig.from_env()
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, provider: str) -> CircuitBreaker:
        with self._lock:
            if provider not in self._breakers:
                self._breakers[provider] = CircuitBreaker(provider, self._config)
            return self._breakers[provider]

    def allow_request(self, provider: str) -> bool:
        return self._get_or_create(provider).allow_request()

    def record_success(self, provider: str) -> None:
        self._get_or_create(provider).record_success()

    def record_failure(self, provider: str) -> None:
        self._get_or_create(provider).record_failure()

    def get_state(self, provider: str) -> CircuitState:
        return self._get_or_create(provider).state

    def reload_config(self) -> None:
        """Thread-safe config reload — re-reads env vars and propagates to all breakers."""
        new_config = CircuitBreakerConfig.from_env()
        with self._lock:
            self._config = new_config
            for breaker in self._breakers.values():
                breaker._config = new_config

    def all_statuses(self) -> Dict[str, Any]:
        with self._lock:
            providers = list(self._breakers.items())
        return {name: cb.status() for name, cb in providers}

    def reset(self, provider: str) -> None:
        self._get_or_create(provider).reset()

    def reset_all(self) -> None:
        with self._lock:
            providers = list(self._breakers.values())
        for cb in providers:
            cb.reset()

    @property
    def enabled(self) -> bool:
        return self._config.enabled


# Module-level singleton
_registry: Optional[CircuitBreakerRegistry] = None
_registry_lock = threading.Lock()


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Return the global circuit breaker registry (created on first call)."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = CircuitBreakerRegistry()
    return _registry


def _reset_registry_for_testing(
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreakerRegistry:
    """Replace the global registry with a fresh instance. ONLY for tests."""
    global _registry
    with _registry_lock:
        _registry = CircuitBreakerRegistry(config=config)
    return _registry


# ===========================================================================
# Rate-limit (429) circuit breaker
#
# Tracks 429 responses per-provider in a rolling window.  When the count
# exceeds the threshold the circuit opens and callers should return 503
# instead of forwarding the request.  The circuit auto-closes after the
# cooldown period.
#
# Configuration via env vars (applied at module import; override per-instance
# via constructor kwargs for tests):
#   TOKENPAK_RATE_LIMIT_WINDOW_SEC   — rolling window length in seconds (default: 60)
#   TOKENPAK_RATE_LIMIT_THRESHOLD    — 429s in window before tripping   (default: 5)
#   TOKENPAK_RATE_LIMIT_COOLDOWN_SEC — seconds to stay open before auto-close (default: 30)
# ===========================================================================

_RL_WINDOW_SEC: float = float(os.environ.get("TOKENPAK_RATE_LIMIT_WINDOW_SEC", "60"))
_RL_THRESHOLD: int = int(os.environ.get("TOKENPAK_RATE_LIMIT_THRESHOLD", "5"))
_RL_COOLDOWN_SEC: float = float(os.environ.get("TOKENPAK_RATE_LIMIT_COOLDOWN_SEC", "30"))


class RateLimitCircuitBreaker:
    """
    Per-provider 429 circuit breaker.

    Records 429 responses in a rolling window.  When the count reaches
    *threshold* within *window_sec* seconds the circuit opens.  While open
    ``is_open()`` returns True — callers should return HTTP 503 without
    forwarding upstream.  After *cooldown_sec* seconds the circuit closes
    automatically and normal forwarding resumes.

    Configuration (env vars at module level, overrideable per-instance):
      TOKENPAK_RATE_LIMIT_WINDOW_SEC   (default 60)
      TOKENPAK_RATE_LIMIT_THRESHOLD    (default 5)
      TOKENPAK_RATE_LIMIT_COOLDOWN_SEC (default 30)
    """

    def __init__(
        self,
        window_sec: Optional[float] = None,
        threshold: Optional[int] = None,
        cooldown_sec: Optional[float] = None,
    ) -> None:
        self._window_sec: float = window_sec if window_sec is not None else _RL_WINDOW_SEC
        self._threshold: int = threshold if threshold is not None else _RL_THRESHOLD
        self._cooldown_sec: float = cooldown_sec if cooldown_sec is not None else _RL_COOLDOWN_SEC
        self._lock = threading.Lock()
        self._429_times: deque = deque()
        self._opened_at: float = 0.0
        self._is_open: bool = False

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_429(self) -> None:
        """Record one 429 response.  Opens circuit when threshold is reached."""
        with self._lock:
            now = time.monotonic()
            self._429_times.append(now)
            # Expire entries outside the rolling window
            cutoff = now - self._window_sec
            while self._429_times and self._429_times[0] < cutoff:
                self._429_times.popleft()
            if not self._is_open and len(self._429_times) >= self._threshold:
                self._is_open = True
                self._opened_at = now

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return True if the circuit is open (caller should return 503)."""
        with self._lock:
            if not self._is_open:
                return False
            # Auto-close after cooldown
            if time.monotonic() - self._opened_at >= self._cooldown_sec:
                self._is_open = False
                self._429_times.clear()
                self._opened_at = 0.0
                return False
            return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Manually reset to closed state.  Use in tests only."""
        with self._lock:
            self._is_open = False
            self._429_times.clear()
            self._opened_at = 0.0

    def status(self) -> Dict[str, Any]:
        """Return a status dict suitable for health-check endpoints."""
        with self._lock:
            now = time.monotonic()
            cooldown_remaining: Optional[float] = None
            if self._is_open:
                remaining = self._cooldown_sec - (now - self._opened_at)
                cooldown_remaining = round(max(0.0, remaining), 1)
            return {
                "is_open": self._is_open,
                "window_sec": self._window_sec,
                "threshold": self._threshold,
                "cooldown_sec": self._cooldown_sec,
                "recent_429s_in_window": len(self._429_times),
                "cooldown_remaining_sec": cooldown_remaining,
            }


class RateLimitCircuitBreakerRegistry:
    """Thread-safe per-provider registry of :class:`RateLimitCircuitBreaker` instances."""

    def __init__(
        self,
        window_sec: Optional[float] = None,
        threshold: Optional[int] = None,
        cooldown_sec: Optional[float] = None,
    ) -> None:
        self._window_sec = window_sec
        self._threshold = threshold
        self._cooldown_sec = cooldown_sec
        self._breakers: Dict[str, RateLimitCircuitBreaker] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, provider: str) -> RateLimitCircuitBreaker:
        with self._lock:
            if provider not in self._breakers:
                self._breakers[provider] = RateLimitCircuitBreaker(
                    window_sec=self._window_sec,
                    threshold=self._threshold,
                    cooldown_sec=self._cooldown_sec,
                )
            return self._breakers[provider]

    def record_429(self, provider: str) -> None:
        """Record a 429 for *provider*.  May open the circuit."""
        self._get_or_create(provider).record_429()

    def is_open(self, provider: str) -> bool:
        """Return True if the rate-limit circuit is open for *provider*."""
        return self._get_or_create(provider).is_open()

    def reset(self, provider: str) -> None:
        self._get_or_create(provider).reset()

    def reset_all(self) -> None:
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.reset()

    def all_statuses(self) -> Dict[str, Any]:
        with self._lock:
            items = list(self._breakers.items())
        return {name: b.status() for name, b in items}


# Module-level singleton for rate-limit circuit breakers
_rl_registry: Optional[RateLimitCircuitBreakerRegistry] = None
_rl_registry_lock = threading.Lock()


def get_rate_limit_registry() -> RateLimitCircuitBreakerRegistry:
    """Return the global rate-limit circuit breaker registry (created on first call)."""
    global _rl_registry
    with _rl_registry_lock:
        if _rl_registry is None:
            _rl_registry = RateLimitCircuitBreakerRegistry()
    return _rl_registry


def _reset_rl_registry_for_testing(
    window_sec: Optional[float] = None,
    threshold: Optional[int] = None,
    cooldown_sec: Optional[float] = None,
) -> RateLimitCircuitBreakerRegistry:
    """Replace the global rate-limit registry with a fresh instance.  ONLY for tests."""
    global _rl_registry
    with _rl_registry_lock:
        _rl_registry = RateLimitCircuitBreakerRegistry(
            window_sec=window_sec,
            threshold=threshold,
            cooldown_sec=cooldown_sec,
        )
    return _rl_registry
