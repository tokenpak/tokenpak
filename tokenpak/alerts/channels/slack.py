"""Slack alert delivery channel (incoming webhooks).

POSTs ``{"text": "..."}`` to a Slack incoming-webhook URL with 3-attempt
exponential-backoff retry (1 s, 2 s, drop). Timeout: 5 s per attempt.

Delivery itself is intentionally ungated here; commercial gating is handled
by higher-level product surfaces, not the low-level transport.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT = 5
_MAX_ATTEMPTS = 3
_MAX_RETRIES = _MAX_ATTEMPTS
_BACKOFF_BASE = 1.0

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "warning": "⚠️",
    "info": "ℹ️",
}


def _build_text(event: str, severity: str, message: str) -> str:
    emoji = _SEVERITY_EMOJI.get(severity, "📢")
    return f"{emoji} *[{event}]* {message}"


def deliver(webhook: str, event: str, severity: str, message: str, **kwargs: Any) -> bool:
    text = _build_text(event, severity, message)
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                logger.debug(
                    "Slack delivered (attempt %d/%d): HTTP %d",
                    attempt,
                    _MAX_RETRIES,
                    resp.status,
                )
                return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
            else:
                logger.error(
                    "Slack delivery failed after %d attempts: %s",
                    _MAX_RETRIES,
                    exc,
                )
    return False


class SlackChannel:
    """Delivers alerts to a Slack incoming-webhook URL."""

    def __init__(self, webhook: str) -> None:
        self.webhook = webhook

    def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool:
        return deliver(self.webhook, event, severity, message, **kwargs)
