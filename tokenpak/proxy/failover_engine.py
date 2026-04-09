"""
TokenPak Failover Engine (F.4, F.5, F.6)

Orchestrates error classification, provider switching, response normalization,
circuit breaking, and failover event logging.

Error Classification:
    rate_limit   (HTTP 429)        → wait + retry same provider, then switch
    server_error (HTTP 500+)       → switch provider immediately
    timeout                        → switch provider
    auth_error   (HTTP 401/403)    → alert, do NOT retry (bad credential)

Circuit Breaker (per provider):
    3 consecutive failures → opens circuit for 5 minutes
    After cool-down: half-open (try once), if succeeds → closed

Failover Events:
    Logged in-memory (thread-safe deque, max 100 events)
    Accessible via FailoverEventLog.get_recent()
    Displayed in `tokenpak status` and stats footer
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .failover import FailoverConfig, FailoverManager, load_failover_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIRCUIT_FAILURE_THRESHOLD = 3  # failures before opening circuit
CIRCUIT_COOL_DOWN_SECONDS = 300  # 5 minutes
RATE_LIMIT_WAIT_SECONDS = 2.0  # initial wait on 429 before retry/switch
MAX_RETRY_SAME_PROVIDER = 1  # retries on same provider before switching


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class ErrorType:
    RATE_LIMIT = "rate_limit"  # HTTP 429
    SERVER_ERROR = "server_error"  # HTTP 500+
    TIMEOUT = "timeout"  # socket/read timeout
    AUTH_ERROR = "auth_error"  # HTTP 401/403
    UNKNOWN = "unknown"


@dataclass
class ClassifiedError:
    error_type: str
    http_status: Optional[int] = None
    message: str = ""

    @property
    def should_switch(self) -> bool:
        """True if the error warrants switching providers (not auth — alert instead)."""
        return self.error_type in (
            ErrorType.SERVER_ERROR,
            ErrorType.TIMEOUT,
            ErrorType.RATE_LIMIT,
        )

    @property
    def is_auth_error(self) -> bool:
        return self.error_type == ErrorType.AUTH_ERROR

    @property
    def is_rate_limit(self) -> bool:
        return self.error_type == ErrorType.RATE_LIMIT


def classify_error(
    http_status: Optional[int] = None,
    exception: Optional[Exception] = None,
) -> ClassifiedError:
    """
    Classify an error into a structured type.

    Args:
        http_status: HTTP response status code (if available)
        exception: Python exception (for timeout / connection errors)

    Returns:
        ClassifiedError with error_type set
    """
    if http_status is not None:
        if http_status == 429:
            return ClassifiedError(
                error_type=ErrorType.RATE_LIMIT,
                http_status=http_status,
                message="Rate limit exceeded (429)",
            )
        if http_status in (401, 403):
            return ClassifiedError(
                error_type=ErrorType.AUTH_ERROR,
                http_status=http_status,
                message=f"Authentication error ({http_status})",
            )
        if http_status >= 500:
            return ClassifiedError(
                error_type=ErrorType.SERVER_ERROR,
                http_status=http_status,
                message=f"Server error ({http_status})",
            )

    if exception is not None:
        exc_name = type(exception).__name__.lower()
        if "timeout" in exc_name or isinstance(exception, TimeoutError):
            return ClassifiedError(
                error_type=ErrorType.TIMEOUT,
                message=f"Timeout: {exception}",
            )
        if "connection" in exc_name or "broken" in exc_name:
            return ClassifiedError(
                error_type=ErrorType.SERVER_ERROR,
                message=f"Connection error: {exception}",
            )

    return ClassifiedError(
        error_type=ErrorType.UNKNOWN,
        http_status=http_status,
        message=str(exception) if exception else "Unknown error",
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitState:
    provider: str
    failure_count: int = 0
    last_failure_ts: float = 0.0
    is_open: bool = False
    half_open_attempt: bool = False  # one probe allowed when cooling down


class CircuitBreaker:
    """
    Per-provider circuit breaker.

    States:
        closed   → normal operation
        open     → skip provider (too many failures)
        half-open → one probe attempt after cool-down
    """

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cool_down_seconds: float = CIRCUIT_COOL_DOWN_SECONDS,
    ) -> None:
        self._threshold = failure_threshold
        self._cool_down = cool_down_seconds
        self._states: Dict[str, CircuitState] = {}
        self._lock = threading.Lock()

    def _get_state(self, provider: str) -> CircuitState:
        if provider not in self._states:
            self._states[provider] = CircuitState(provider=provider)
        return self._states[provider]

    def is_available(self, provider: str) -> bool:
        """True if the circuit allows requests to this provider."""
        with self._lock:
            state = self._get_state(provider)
            if not state.is_open:
                return True
            # Check if cool-down has elapsed
            elapsed = time.monotonic() - state.last_failure_ts
            if elapsed >= self._cool_down:
                if not state.half_open_attempt:
                    # Allow one probe
                    state.half_open_attempt = True
                    return True
                # Already probing — keep blocked until result
                return False
            return False

    def record_failure(self, provider: str) -> bool:
        """
        Record a failure for a provider.

        Returns True if the circuit just opened (threshold crossed).
        """
        with self._lock:
            state = self._get_state(provider)
            state.failure_count += 1
            state.last_failure_ts = time.monotonic()
            state.half_open_attempt = False
            if state.failure_count >= self._threshold and not state.is_open:
                state.is_open = True
                logger.warning(
                    "Circuit OPEN for provider %r after %d failures — " "will retry in %ds",
                    provider,
                    state.failure_count,
                    int(self._cool_down),
                )
                return True
            return False

    def record_success(self, provider: str) -> None:
        """Record a success — resets failure count and closes circuit."""
        with self._lock:
            state = self._get_state(provider)
            state.failure_count = 0
            state.is_open = False
            state.half_open_attempt = False

    def get_state(self, provider: str) -> Dict[str, Any]:
        """Return current circuit state dict for status display."""
        with self._lock:
            state = self._get_state(provider)
            return {
                "provider": state.provider,
                "is_open": state.is_open,
                "failure_count": state.failure_count,
                "seconds_until_retry": (
                    max(0, int(self._cool_down - (time.monotonic() - state.last_failure_ts)))
                    if state.is_open
                    else 0
                ),
            }

    def reset(self, provider: str) -> None:
        """Force-reset circuit to closed (for testing / manual override)."""
        with self._lock:
            self._states[provider] = CircuitState(provider=provider)


# ---------------------------------------------------------------------------
# Failover event log
# ---------------------------------------------------------------------------


@dataclass
class FailoverEvent:
    timestamp: str
    original_provider: str
    failover_provider: str
    error_type: str
    http_status: Optional[int]
    model: str
    succeeded: bool
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "original_provider": self.original_provider,
            "failover_provider": self.failover_provider,
            "error_type": self.error_type,
            "http_status": self.http_status,
            "model": self.model,
            "succeeded": self.succeeded,
            "message": self.message,
        }


class FailoverEventLog:
    """Thread-safe in-memory log of failover events (max 100)."""

    _MAX_EVENTS = 100

    def __init__(self) -> None:
        from collections import deque

        self._events: deque = deque(maxlen=self._MAX_EVENTS)
        self._lock = threading.Lock()

    def record(self, event: FailoverEvent) -> None:
        with self._lock:
            self._events.append(event)
        logger.info(
            "FAILOVER: %s → %s (%s) model=%s succeeded=%s",
            event.original_provider,
            event.failover_provider,
            event.error_type,
            event.model,
            event.succeeded,
        )

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        return [e.to_dict() for e in reversed(events[-limit:])]

    def get_footer_indicator(self) -> Optional[str]:
        """Return footer string for the most recent failover event, or None."""
        with self._lock:
            if not self._events:
                return None
            ev = self._events[-1]
        status_code = f" {ev.http_status}" if ev.http_status else ""
        return (
            f"⚠️ failover:{ev.failover_provider} "
            f"({ev.original_provider}{status_code} {ev.error_type})"
        )


# Module-level singleton log
_event_log = FailoverEventLog()


def get_event_log() -> FailoverEventLog:
    """Return the global failover event log."""
    return _event_log


# ---------------------------------------------------------------------------
# Failover engine
# ---------------------------------------------------------------------------


@dataclass
class ProviderAttempt:
    """Information for one attempt in the failover loop."""

    provider: str
    model: str
    credential_env: str
    is_primary: bool
    skipped_providers: List[str] = field(default_factory=list)


@dataclass
class FailoverDecision:
    """Result of evaluating an error against decision logic."""

    action: str  # "retry_wait" | "switch" | "alert_abort" | "abort"
    wait_seconds: float = 0.0
    reason: str = ""


def decide(error: ClassifiedError) -> FailoverDecision:
    """
    Evaluate an error and return the appropriate action.

    Decision table:
        rate_limit  → wait RATE_LIMIT_WAIT_SECONDS, then switch
        server_error → switch immediately
        timeout     → switch immediately
        auth_error  → alert, abort (don't retry)
        unknown     → switch (optimistic)
    """
    if error.is_rate_limit:
        return FailoverDecision(
            action="switch",
            wait_seconds=RATE_LIMIT_WAIT_SECONDS,
            reason=f"Rate limit ({error.http_status}) — waiting {RATE_LIMIT_WAIT_SECONDS}s then switching",
        )
    if error.is_auth_error:
        return FailoverDecision(
            action="alert_abort",
            reason=f"Auth error ({error.http_status}) — check credentials, aborting failover",
        )
    if error.error_type in (ErrorType.SERVER_ERROR, ErrorType.TIMEOUT, ErrorType.UNKNOWN):
        return FailoverDecision(
            action="switch",
            reason=f"{error.error_type} — switching provider",
        )
    return FailoverDecision(action="switch", reason="Unhandled error — switching")


class FailoverEngine:
    """
    Orchestrates multi-provider failover for LLM proxy requests.

    Usage::

        engine = FailoverEngine()
        for attempt in engine.iter_attempts(original_model="claude-sonnet-4-5",
                                             original_provider="anthropic"):
            try:
                response = call_provider(attempt.provider, attempt.model, ...)
                engine.record_success(attempt.provider)
                break
            except ProviderError as exc:
                error = classify_error(http_status=exc.status)
                if not engine.handle_error(attempt, error):
                    raise  # all providers exhausted
    """

    def __init__(
        self,
        config: Optional[FailoverConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        event_log: Optional[FailoverEventLog] = None,
    ) -> None:
        self._manager = FailoverManager(config=config or load_failover_config())
        self._circuit = circuit_breaker or CircuitBreaker()
        self._log = event_log or _event_log

    @property
    def enabled(self) -> bool:
        return self._manager.enabled

    def iter_attempts(
        self,
        original_model: str,
        original_provider: str,
    ) -> Iterator[ProviderAttempt]:
        """
        Yield ProviderAttempt objects in failover order, respecting circuit breakers.

        The first attempt is always the original provider.
        Subsequent attempts are from the failover chain, skipping open circuits.

        Args:
            original_model: Model name in the original request
            original_provider: Provider of the original request
        """
        if not self._manager.enabled:
            # No failover configured — yield only the original
            yield ProviderAttempt(
                provider=original_provider,
                model=original_model,
                credential_env="",
                is_primary=True,
            )
            return

        skipped: List[str] = []
        first = True

        for result in self._manager.iter_providers(original_model, preferred=original_provider):
            if not self._circuit.is_available(result.provider):
                logger.debug("Circuit OPEN — skipping provider %r", result.provider)
                skipped.append(result.provider)
                continue

            yield ProviderAttempt(
                provider=result.provider,
                model=result.model,
                credential_env=result.credential_env,
                is_primary=first,
                skipped_providers=list(skipped),
            )
            first = False

    def handle_error(
        self,
        attempt: ProviderAttempt,
        error: ClassifiedError,
        original_provider: str,
        original_model: str,
    ) -> Tuple[bool, float]:
        """
        Process an error from a provider attempt.

        Records the failure, logs the event, updates circuit breaker.

        Args:
            attempt: The provider attempt that failed
            error: Classified error
            original_provider: The original (primary) provider
            original_model: The original model name

        Returns:
            (should_continue, wait_seconds)
            - should_continue: True if caller should try the next provider
            - wait_seconds: How long to wait before next attempt (0 if none)
        """
        decision = decide(error)

        # Record circuit failure
        self._circuit.record_failure(attempt.provider)

        # Log failover event (only when switching away from original)
        if not attempt.is_primary or decision.action in ("switch",):
            ts = datetime.now(timezone.utc).isoformat()
            ev = FailoverEvent(
                timestamp=ts,
                original_provider=original_provider,
                failover_provider=attempt.provider,
                error_type=error.error_type,
                http_status=error.http_status,
                model=original_model,
                succeeded=False,
                message=decision.reason,
            )
            self._log.record(ev)

        if decision.action == "alert_abort":
            logger.error(
                "Auth error on provider %r — check %r env var. Aborting failover.",
                attempt.provider,
                attempt.credential_env,
            )
            return False, 0.0

        if decision.action == "switch":
            return True, decision.wait_seconds

        return True, decision.wait_seconds

    def record_success(
        self,
        provider: str,
        original_provider: str,
        original_model: str,
        was_failover: bool = False,
    ) -> Optional[str]:
        """
        Record a successful provider call.

        Updates circuit breaker and (if this was a failover) logs the event.

        Args:
            provider: Provider that succeeded
            original_provider: Original primary provider
            original_model: Original model name
            was_failover: True if this was not the primary provider

        Returns:
            Footer indicator string if this was a failover, else None
        """
        self._circuit.record_success(provider)

        if was_failover:
            ts = datetime.now(timezone.utc).isoformat()
            ev = FailoverEvent(
                timestamp=ts,
                original_provider=original_provider,
                failover_provider=provider,
                error_type="",
                http_status=None,
                model=original_model,
                succeeded=True,
                message=f"Served by {provider} after {original_provider} failed",
            )
            self._log.record(ev)
            return self._log.get_footer_indicator()
        return None

    def get_circuit_states(self) -> List[Dict[str, Any]]:
        """Return all known circuit breaker states (for status display)."""
        states = []
        config = self._manager._config
        for entry in config.chain:
            states.append(self._circuit.get_state(entry.provider))
        return states


# ---------------------------------------------------------------------------
# Response normalization wrapper
# ---------------------------------------------------------------------------


def normalize_response(
    response_body: Dict[str, Any],
    source_provider: str,
    target_provider: str,
) -> Dict[str, Any]:
    """
    Normalize a provider response back to the original provider's format.

    This is a thin wrapper around translate_response that logs translation.

    Args:
        response_body: Parsed response dict from the failover provider
        source_provider: Provider that served the response
        target_provider: Provider the client expects (original provider)

    Returns:
        Response dict in target_provider format
    """
    if source_provider == target_provider:
        return response_body

    from .providers.translator import translate_response

    try:
        normalized = translate_response(response_body, source_provider, target_provider)
        logger.debug(
            "Normalized response from %r to %r format",
            source_provider,
            target_provider,
        )
        return normalized
    except (ValueError, KeyError) as exc:
        logger.warning(
            "Could not normalize response %r → %r: %s — passing through raw",
            source_provider,
            target_provider,
            exc,
        )
        return response_body


def normalize_stream(
    source_provider: str,
    target_provider: str,
):
    """
    Return a StreamingTranslator for failover stream normalization.

    Returns None if no translation is needed.
    """
    if source_provider == target_provider:
        return None

    from .providers.stream_translator import StreamingTranslator

    try:
        return StreamingTranslator(source_provider, target_provider)
    except ValueError as exc:
        logger.warning("No streaming translator: %s — stream passthrough", exc)
        return None


# ---------------------------------------------------------------------------
# Footer rendering
# ---------------------------------------------------------------------------


def render_failover_footer(
    original_provider: str,
    http_status: Optional[int],
    error_type: str,
    failover_provider: str,
) -> str:
    """
    Render the failover footer indicator for the stats footer.

    Example: '⚠️ failover:openai (anthropic 429 rate_limit)'
    """
    status_str = f" {http_status}" if http_status else ""
    return f"⚠️ failover:{failover_provider} " f"({original_provider}{status_str} {error_type})"
