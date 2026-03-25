"""
TokenPak Auth Guard — Phase 1: Detection + Alerts

Tracks consecutive authentication failures per provider.
On threshold breach, emits an auth-failure-detected event so
downstream hooks (e.g. Telegram alerts) can notify the user.

Usage in proxy.py:
    from tokenpak.auth_guard import AUTH_GUARD
    AUTH_GUARD.record_response(provider="anthropic", status_code=status)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (override via env vars)
# ---------------------------------------------------------------------------
AUTH_FAILURE_THRESHOLD = int(os.environ.get("TOKENPAK_AUTH_FAILURE_THRESHOLD", "3"))
AUTH_ALERT_COOLDOWN_SEC = int(os.environ.get("TOKENPAK_AUTH_ALERT_COOLDOWN", "300"))  # 5 min
INCIDENT_LOG_PATH = Path(os.environ.get(
    "TOKENPAK_INCIDENT_LOG",
    os.path.expanduser("~/.tokenpak/incidents.log")
))


class AuthGuard:
    """
    Thread-safe tracker for consecutive auth failures per provider.

    - Records HTTP 401/403 responses from upstream providers.
    - After AUTH_FAILURE_THRESHOLD consecutive failures, emits an event.
    - Emits at most once per AUTH_ALERT_COOLDOWN_SEC seconds (spam protection).
    - Resets counter on any successful (non-401/403) response.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # provider_name → consecutive failure count
        self._counters: Dict[str, int] = {}
        # provider_name → last alert timestamp (epoch float)
        self._last_alert: Dict[str, float] = {}
        # Registered event handlers: (provider, event_name, details) -> None
        self._handlers: list = []

    def on_auth_failure(self, handler: Callable[[str, str, dict], None]) -> None:
        """Register a callback for auth-failure-detected events.

        handler(provider: str, event: str, details: dict) -> None
        """
        with self._lock:
            self._handlers.append(handler)

    def record_response(self, provider: str, status_code: int) -> None:
        """Call this for every upstream response.

        Args:
            provider: Upstream name, e.g. "anthropic", "openai"
            status_code: HTTP status returned by upstream
        """
        is_auth_failure = status_code in (401, 403)
        with self._lock:
            if is_auth_failure:
                self._counters[provider] = self._counters.get(provider, 0) + 1
                count = self._counters[provider]
                logger.debug(
                    "auth_guard: %s consecutive auth failures for %s",
                    count,
                    provider,
                )
                if count >= AUTH_FAILURE_THRESHOLD:
                    self._maybe_emit(provider, count)
            else:
                # Reset on success
                if self._counters.get(provider, 0) > 0:
                    logger.debug("auth_guard: reset counter for %s (status %s)", provider, status_code)
                self._counters[provider] = 0

    def _maybe_emit(self, provider: str, count: int) -> None:
        """Emit auth-failure-detected event if cooldown has elapsed."""
        now = time.time()
        last = self._last_alert.get(provider, 0.0)
        if now - last < AUTH_ALERT_COOLDOWN_SEC:
            remaining = int(AUTH_ALERT_COOLDOWN_SEC - (now - last))
            logger.debug(
                "auth_guard: alert suppressed for %s (cooldown %ds remaining)",
                provider, remaining,
            )
            return

        self._last_alert[provider] = now
        details = {
            "provider": provider,
            "consecutive_failures": count,
            "threshold": AUTH_FAILURE_THRESHOLD,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        # Log incident
        try:
            self._log_incident(details)
        except Exception as exc:
            logger.error("auth_guard: failed to log incident: %s", exc)

        # Fire handlers (outside lock — handlers may block)
        handlers = list(self._handlers)

        # Run handlers in a background thread so proxy isn't blocked
        def _run_handlers():
            for h in handlers:
                try:
                    h(provider, "auth-failure-detected", details)
                except Exception as exc:
                    logger.error("auth_guard: handler error: %s", exc)

        t = threading.Thread(target=_run_handlers, daemon=True, name="auth-guard-alert")
        t.start()

    def _log_incident(self, details: dict) -> None:
        """Append incident to ~/.tokenpak/incidents.log"""
        INCIDENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(details)
        with open(INCIDENT_LOG_PATH, "a") as f:
            f.write(line + "\n")
        logger.info("auth_guard: incident logged → %s", INCIDENT_LOG_PATH)

    # ------------------------------------------------------------------
    # Introspection helpers (for /stats or tests)
    # ------------------------------------------------------------------
    def get_counters(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def get_last_alert_times(self) -> dict:
        with self._lock:
            return dict(self._last_alert)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
AUTH_GUARD = AuthGuard()
