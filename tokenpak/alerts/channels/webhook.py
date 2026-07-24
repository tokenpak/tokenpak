"""Webhook alert delivery channel.

POSTs a generic JSON payload to a configured URL with 3-attempt
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
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT = 5
_MAX_ATTEMPTS = 3
_MAX_RETRIES = _MAX_ATTEMPTS
_BACKOFF_BASE = 1.0


def _build_payload(event: str, severity: str, message: str, **kwargs: Any) -> bytes:
    payload: dict[str, Any] = {
        "event": event,
        "severity": severity,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(kwargs)
    return json.dumps(payload).encode()


def deliver(url: str, event: str, severity: str, message: str, **kwargs: Any) -> bool:
    body = _build_payload(event, severity, message, **kwargs)
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
                    "Webhook delivered (attempt %d/%d): HTTP %d",
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
                    "Webhook delivery failed after %d attempts: %s",
                    _MAX_RETRIES,
                    exc,
                )
    return False


class WebhookChannel:
    """Delivers alerts as generic JSON POST requests."""

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool:
        return deliver(self.url, event, severity, message, **kwargs)
