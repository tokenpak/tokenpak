"""
TokenPak Auth Alert — Generic Notification Hook

Provides a pluggable notification system for auth failure events emitted
by AuthGuard. Ships with a WebhookNotificationHook (generic HTTP POST) and
a no-op NullNotificationHook.

Usage — register any callable as a handler:

    from tokenpak.auth_alert import register_auth_alert_hook, WebhookNotificationHook

    # Option 1: Generic webhook (any HTTP endpoint)
    hook = WebhookNotificationHook(
        url="https://your-service.com/alerts",
        headers={"Authorization": "Bearer your-token"},
    )
    register_auth_alert_hook(hook)

    # Option 2: Custom callable
    def my_handler(provider: str, event: str, details: dict) -> None:
        print(f"Auth failure: {provider} — {details}")

    register_auth_alert_hook(my_handler)

    # Option 3: Adapter-specific hooks (see examples/06_auth_alerts.py)
    # Telegram, Slack, PagerDuty, etc. wire here using the same interface.

Env vars:
    TOKENPAK_ALERT_WEBHOOK_URL  — if set, auto-registers a WebhookNotificationHook at startup
    TOKENPAK_ALERT_WEBHOOK_HEADERS — JSON string of extra headers (optional)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public hook protocol
# ---------------------------------------------------------------------------
# A notification hook is any callable with the signature:
#   (provider: str, event: str, details: dict) -> None
# This matches the AuthGuard.on_auth_failure handler interface exactly.
NotificationHook = Callable[[str, str, dict], None]


# ---------------------------------------------------------------------------
# Built-in hook: Generic HTTP webhook
# ---------------------------------------------------------------------------


class WebhookNotificationHook:
    """Send auth-failure alerts as JSON POST to any HTTP endpoint.

    Args:
        url: The webhook URL to POST to.
        headers: Optional extra HTTP headers (e.g. Authorization).
        timeout: Request timeout in seconds (default: 15).

    Example::

        hook = WebhookNotificationHook(
            url="https://hooks.slack.com/services/...",
            headers={"Content-Type": "application/json"},
        )
        register_auth_alert_hook(hook)
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def __call__(self, provider: str, event: str, details: dict) -> None:
        if event != "auth-failure-detected":
            return
        payload = json.dumps(
            {
                "event": event,
                "provider": provider,
                "details": details,
                "message": _build_alert_message(provider, details),
            }
        ).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json", **self.headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                if status < 300:
                    logger.info("auth_alert: webhook delivered (HTTP %s) to %s", status, self.url)
                else:
                    logger.warning("auth_alert: webhook returned HTTP %s from %s", status, self.url)
        except Exception as exc:
            logger.error("auth_alert: webhook delivery failed to %s — %s", self.url, exc)


# ---------------------------------------------------------------------------
# Built-in hook: No-op (useful for testing or explicit disablement)
# ---------------------------------------------------------------------------


class NullNotificationHook:
    """A no-op hook — swallows all events. Useful for testing."""

    def __call__(self, provider: str, event: str, details: dict) -> None:
        logger.debug(
            "auth_alert: NullNotificationHook received event=%s provider=%s", event, provider
        )


# ---------------------------------------------------------------------------
# Message builder (public — tested directly in test_auth_guard.py)
# ---------------------------------------------------------------------------


def _build_alert_message(provider: str, details: dict) -> str:
    """Build a human-readable alert message for an auth failure event."""
    count = details.get("consecutive_failures", "?")
    ts = details.get("timestamp", "unknown")
    provider_display = provider.capitalize()
    return (
        f"TokenPak Auth Failure — {provider_display} token is expired or revoked.\n"
        f"Proxy is OFFLINE. Requests now bypass compression (2-3x cost).\n\n"
        f"Fix: update your {provider_display} API key and restart the proxy.\n"
        f"  Script: update-anthropic-token.sh\n\n"
        f"Details: {count} consecutive 401/403 from {provider_display} @ {ts}"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_auth_alert_hook(hook: NotificationHook) -> None:
    """Register a notification hook with the global AUTH_GUARD singleton.

    The hook is called whenever AuthGuard detects an auth failure threshold
    breach. Multiple hooks can be registered; all are called in order.

    Args:
        hook: Any callable with signature (provider, event, details) -> None,
              or an instance of WebhookNotificationHook / NullNotificationHook.

    Example::

        from tokenpak.auth_alert import register_auth_alert_hook, WebhookNotificationHook

        register_auth_alert_hook(WebhookNotificationHook(
            url="https://your-endpoint.com/tokenpak-alerts",
        ))
    """
    from tokenpak.auth_guard import AUTH_GUARD  # noqa: PLC0415 (lazy import — avoids circular)

    AUTH_GUARD.on_auth_failure(hook)
    logger.info("auth_alert: registered notification hook: %s", type(hook).__name__)


def _auto_register_from_env() -> None:
    """Auto-register a WebhookNotificationHook if TOKENPAK_ALERT_WEBHOOK_URL is set.

    Called at proxy startup. Users who prefer explicit registration should
    call register_auth_alert_hook() directly instead of using env vars.
    """
    url = os.environ.get("TOKENPAK_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    headers: Dict[str, str] = {}
    raw_headers = os.environ.get("TOKENPAK_ALERT_WEBHOOK_HEADERS", "").strip()
    if raw_headers:
        try:
            headers = json.loads(raw_headers)
        except json.JSONDecodeError:
            logger.warning(
                "auth_alert: TOKENPAK_ALERT_WEBHOOK_HEADERS is not valid JSON — ignoring"
            )
    hook = WebhookNotificationHook(url=url, headers=headers)
    register_auth_alert_hook(hook)
    logger.info("auth_alert: auto-registered WebhookNotificationHook from env (url=%s)", url)
