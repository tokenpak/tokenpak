"""
TokenPak Circuit Breaker

Implements the classic circuit breaker pattern for per-provider fault isolation.
Prevents cascade failures when upstream LLM providers are unhealthy.

States
------
CLOSED    → Normal operation. Requests pass through. Failures are tracked.
OPEN      → Provider unhealthy. Requests are fast-failed immediately (503).
HALF_OPEN → Recovery probe. One test request is allowed through.
            Success → CLOSED. Failure → OPEN (reset timer).

Configuration (via env vars)
----------------------------
TOKENPAK_CB_FAILURE_THRESHOLD   Number of failures in window before tripping (default: 5)
TOKENPAK_CB_RECOVERY_TIMEOUT    Seconds before OPEN → HALF_OPEN probe (default: 60)
TOKENPAK_CB_WINDOW_SECONDS      Failure counting window in seconds (default: 60)
TOKENPAK_CB_ENABLED             Set to 0 to disable circuit breakers entirely (default: 1)

Usage::

    from tokenpak.agent.proxy.circuit_breaker import get_circuit_breaker_registry

    registry = get_circuit_breaker_registry()

    # Before forwarding request:
    if not registry.allow_request("anthropic"):
        # Return 503 immediately
        ...

    # After success:
    registry.record_success("anthropic")

    # After failure:
    registry.record_failure("anthropic")
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Fast-failing
    HALF_OPEN = "half_open"  # Probing recovery


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreakerConfig:
    """Configuration for all circuit breakers."""

    enabled: bool = True
    failure_threshold: int = 5  # failures in window before tripping
    recovery_timeout: float = 60.0  # seconds before OPEN → HALF_OPEN
    window_seconds: float = 60.0  # rolling failure counting window

    @classmethod
    def from_env(cls) -> "CircuitBreakerConfig":
        return cls(
            enabled=os.environ.get("TOKENPAK_CB_ENABLED", "1") != "0",
            failure_threshold=int(os.environ.get("TOKENPAK_CB_FAILURE_THRESHOLD", "5")),
            recovery_timeout=float(os.environ.get("TOKENPAK_CB_RECOVERY_TIMEOUT", "60")),
            window_seconds=float(os.environ.get("TOKENPAK_CB_WINDOW_SECONDS", "60")),
        )


# ---------------------------------------------------------------------------
# Per-provider circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Thread-safe circuit breaker for a single provider.

    State machine::

        CLOSED  ──(threshold failures in window)──▶  OPEN
        OPEN    ──(recovery_timeout elapsed)──────▶  HALF_OPEN
        HALF_OPEN ─(success)─▶  CLOSED
        HALF_OPEN ─(failure)─▶  OPEN  (timer reset)
    """

    def __init__(
        self,
        provider: str,
        config: CircuitBreakerConfig,
    ) -> None:
        self.provider = provider
        self._config = config
        self._lock = threading.Lock()

        self._state: CircuitState = CircuitState.CLOSED
        # Timestamps of failures within the rolling window
        self._failure_times: deque = deque()
        # When the circuit was opened (for recovery timeout)
        self._opened_at: float = 0.0
        # Whether a half-open probe is in flight
        self._probe_in_flight: bool = False
        # Lifetime counters
        self._total_trips: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow_request(self) -> bool:
        """
        Returns True if the request should proceed, False to fast-fail.

        Side effect: transitions OPEN → HALF_OPEN when recovery timeout
        has elapsed and sets ``_probe_in_flight = True`` for the probe.
        """
        if not self._config.enabled:
            return True

        with self._lock:
            now = time.monotonic()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = now - self._opened_at
                if elapsed >= self._config.recovery_timeout:
                    # Transition to HALF_OPEN and allow exactly one probe
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = True
                    return True
                # Still OPEN — fast-fail
                return False

            if self._state == CircuitState.HALF_OPEN:
                if not self._probe_in_flight:
                    # Allow one probe at a time
                    self._probe_in_flight = True
                    return True
                # Probe already in flight — fast-fail concurrent requests
                return False

        return True  # unreachable, but satisfies type checker

    def record_success(self) -> None:
        """Record a successful response. Resets circuit if in HALF_OPEN."""
        with self._lock:
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                # Recovery confirmed — close the circuit
                self._state = CircuitState.CLOSED
                self._failure_times.clear()
                self._probe_in_flight = False

    def record_failure(self) -> None:
        """
        Record a failed response. May trip the circuit.

        - In CLOSED: add failure timestamp; trip if threshold exceeded in window.
        - In HALF_OPEN: probe failed → back to OPEN (reset timer).
        - In OPEN: ignore (already open).
        """
        with self._lock:
            now = time.monotonic()
            self._total_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — reopen
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._probe_in_flight = False
                return

            if self._state == CircuitState.OPEN:
                return  # Already open, nothing to do

            # CLOSED: track failure
            self._failure_times.append(now)
            # Purge stale failures outside the rolling window
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
        """Manually reset the circuit to CLOSED. Admin use only."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_times.clear()
            self._probe_in_flight = False
            self._opened_at = 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Known provider URL substrings → canonical provider names
_PROVIDER_PATTERNS: List[tuple[str, str]] = [
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
    # Fall back to the hostname as-is
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


class CircuitBreakerRegistry:
    """
    Thread-safe registry of per-provider circuit breakers.

    Breakers are created on first access and reused thereafter.
    """

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
        """Returns True if the request should proceed for this provider."""
        return self._get_or_create(provider).allow_request()

    def record_success(self, provider: str) -> None:
        """Record a successful request for this provider."""
        self._get_or_create(provider).record_success()

    def record_failure(self, provider: str) -> None:
        """Record a failed request for this provider."""
        self._get_or_create(provider).record_failure()

    def get_state(self, provider: str) -> CircuitState:
        """Return the current circuit state for this provider."""
        return self._get_or_create(provider).state

    def all_statuses(self) -> Dict[str, Any]:
        """Return status dicts for all known providers."""
        with self._lock:
            providers = list(self._breakers.items())
        return {name: cb.status() for name, cb in providers}

    def reset(self, provider: str) -> None:
        """Manually reset a specific provider's circuit."""
        self._get_or_create(provider).reset()

    def reset_all(self) -> None:
        """Reset all circuits to CLOSED."""
        with self._lock:
            providers = list(self._breakers.values())
        for cb in providers:
            cb.reset()

    @property
    def enabled(self) -> bool:
        return self._config.enabled


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

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
    """
    Replace the global registry with a fresh instance.
    ONLY for use in tests.
    """
    global _registry
    with _registry_lock:
        _registry = CircuitBreakerRegistry(config=config)
    return _registry
