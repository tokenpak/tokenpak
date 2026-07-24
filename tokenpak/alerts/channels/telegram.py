"""Telegram alert delivery channel (Bot API sendMessage).

POSTs ``{"chat_id": ..., "text": "..."}`` to the Telegram Bot API with
3-attempt exponential-backoff retry (1 s, 2 s, drop). Timeout: 5 s per attempt.

Configuration (env vars or config file):
- ``TOKENPAK_TELEGRAM_BOT_TOKEN``: bot token issued by @BotFather
- ``TOKENPAK_TELEGRAM_CHAT_ID``: target chat, channel, or group ID
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
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_API_BASE = "https://api.telegram.org"

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "warning": "⚠️",
    "info": "ℹ️",
}


def _build_text(event: str, severity: str, message: str) -> str:
    emoji = _SEVERITY_EMOJI.get(severity, "📢")
    return f"{emoji} [{event}] {message}"


def deliver(
    token: str,
    chat_id: str,
    event: str,
    severity: str,
    message: str,
    **kwargs: Any,
) -> bool:
    """Send an alert to Telegram via Bot API ``sendMessage``."""
    text = _build_text(event, severity, message)
    url = f"{_API_BASE}/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                logger.debug(
                    "Telegram delivered (attempt %d/%d): HTTP %d",
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
                    "Telegram delivery failed after %d attempts: %s",
                    _MAX_RETRIES,
                    exc,
                )
    return False


class TelegramChannel:
    """Delivers alerts to a Telegram chat via Bot API."""

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool:
        return deliver(self.token, self.chat_id, event, severity, message, **kwargs)
