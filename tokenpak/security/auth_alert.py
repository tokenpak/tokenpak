"""
TokenPak Auth Alert — Generic Notification Hook

Provides a pluggable notification system for auth failure events emitted
by AuthGuard. Ships with a WebhookNotificationHook (generic HTTP POST) and
a no-op NullNotificationHook.

Usage — register any callable as a handler:

    from tokenpak.security.auth_alert import register_auth_alert_hook, WebhookNotificationHook

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
from typing import Callable, Optional, cast

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telegram delivery config (optional direct-API fallback)
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.environ.get("TOKENPAK_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TOKENPAK_TELEGRAM_CHAT_ID", "")


# ---------------------------------------------------------------------------
# Public hook protocol
# ---------------------------------------------------------------------------
# A notification hook is any callable with the signature:
#   (provider: str, event: str, details: dict) -> None
# This matches the AuthGuard.on_auth_failure handler interface exactly.
AuthFailureDetails = dict[str, object]
NotificationHook = Callable[[str, str, AuthFailureDetails], None]


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
        headers: Optional[dict[str, str]] = None,
        timeout: int = 15,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def __call__(self, provider: str, event: str, details: AuthFailureDetails) -> None:
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

    def __call__(self, provider: str, event: str, details: AuthFailureDetails) -> None:
        logger.debug(
            "auth_alert: NullNotificationHook received event=%s provider=%s", event, provider
        )


# ---------------------------------------------------------------------------
# Message builder (public — tested directly in test_auth_guard.py)
# ---------------------------------------------------------------------------


def _build_alert_message(provider: str, details: AuthFailureDetails) -> str:
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
# Telegram delivery helper
# ---------------------------------------------------------------------------


def _send_telegram(text: str, chat_id: Optional[str] = None) -> bool:
    """Send a Telegram message via direct Bot API.

    Returns True on success, False on failure.

    Args:
        text: Message text to send.
        chat_id: Optional Telegram chat ID. Falls back to TOKENPAK_TELEGRAM_CHAT_ID env var.
    """
    target_chat = chat_id or TELEGRAM_CHAT_ID

    bot_token = TELEGRAM_BOT_TOKEN
    if not bot_token:
        logger.warning("auth_alert: no Telegram bot token configured (TOKENPAK_TELEGRAM_BOT_TOKEN)")
        return False

    if not target_chat:
        logger.warning("auth_alert: no Telegram chat ID configured (TOKENPAK_TELEGRAM_CHAT_ID)")
        return False

    try:
        payload = json.dumps({"chat_id": target_chat, "text": text}).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            loaded: object = json.loads(resp.read())
            body = cast(dict[str, object], loaded) if isinstance(loaded, dict) else {}
            if body.get("ok") is True:
                logger.info("auth_alert: Telegram alert sent via direct API")
                return True
            logger.warning("auth_alert: Telegram API returned ok=false: %s", body)
            return False
    except Exception as exc:
        logger.error("auth_alert: Telegram direct API delivery failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Built-in Telegram auth-failure handler
# ---------------------------------------------------------------------------


def _on_auth_failure(provider: str, event: str, details: AuthFailureDetails) -> None:
    """Default handler: sends a Telegram alert when auth-failure-detected fires.

    Registered automatically by register_auth_alert_hook().
    Only acts on event == "auth-failure-detected"; all other events are ignored.
    """
    if event != "auth-failure-detected":
        return
    message = _build_alert_message(provider, details)
    _send_telegram(message)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_auth_alert_hook(hook: Optional[NotificationHook] = None) -> None:
    """Register a notification hook with the global AUTH_GUARD singleton.

    When called with no arguments, registers the built-in ``_on_auth_failure``
    Telegram handler. Pass a custom *hook* callable to register a different
    handler instead.

    The hook is called whenever AuthGuard detects an auth failure threshold
    breach. Multiple hooks can be registered; all are called in order.

    Args:
        hook: Any callable with signature (provider, event, details) -> None,
              or an instance of WebhookNotificationHook / NullNotificationHook.
              Defaults to the built-in ``_on_auth_failure`` Telegram handler.

    Example::

        from tokenpak.security.auth_alert import register_auth_alert_hook, WebhookNotificationHook

        # Register built-in Telegram handler:
        register_auth_alert_hook()

        # Register a custom webhook handler:
        register_auth_alert_hook(WebhookNotificationHook(
            url="https://your-endpoint.com/tokenpak-alerts",
        ))
    """
    from tokenpak.security.auth_guard import (
        AUTH_GUARD,  # noqa: PLC0415 (lazy import — avoids circular)
    )

    effective_hook = hook if hook is not None else _on_auth_failure
    AUTH_GUARD.on_auth_failure(effective_hook)
    logger.info(
        "auth_alert: registered notification hook: %s",
        getattr(effective_hook, "__name__", type(effective_hook).__name__),
    )


def _auto_register_from_env() -> None:
    """Auto-register a WebhookNotificationHook if TOKENPAK_ALERT_WEBHOOK_URL is set.

    Called at proxy startup. Users who prefer explicit registration should
    call register_auth_alert_hook() directly instead of using env vars.
    """
    url = os.environ.get("TOKENPAK_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    headers: dict[str, str] = {}
    raw_headers = os.environ.get("TOKENPAK_ALERT_WEBHOOK_HEADERS", "").strip()
    if raw_headers:
        try:
            loaded_headers: object = json.loads(raw_headers)
            if isinstance(loaded_headers, dict) and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in loaded_headers.items()
            ):
                headers = cast(dict[str, str], loaded_headers)
            else:
                logger.warning(
                    "auth_alert: TOKENPAK_ALERT_WEBHOOK_HEADERS must map strings to strings — ignoring"
                )
        except json.JSONDecodeError:
            logger.warning(
                "auth_alert: TOKENPAK_ALERT_WEBHOOK_HEADERS is not valid JSON — ignoring"
            )
    hook = WebhookNotificationHook(url=url, headers=headers)
    register_auth_alert_hook(hook)
    logger.info("auth_alert: auto-registered WebhookNotificationHook from env (url=%s)", url)
