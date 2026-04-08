"""Slack alert delivery channel (incoming webhooks).

POSTs ``{"text": "..."}`` to a Slack incoming-webhook URL with 3-attempt
exponential-backoff retry (1 s, 2 s, drop).  Timeout: 5 s per attempt.

Budget alerts are a Pro feature — gated via @requires_tier.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from tokenpak.license.gates import requires_tier
from tokenpak.license.tier import LicenseTier

logger = logging.getLogger(__name__)

_TIMEOUT = 5
_MAX_ATTEMPTS = 3

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "warning": "⚠️",
    "info": "ℹ️",
}


class SlackChannel:
    """Delivers alerts to a Slack incoming-webhook URL."""

    def __init__(self, webhook: str) -> None:
        self.webhook = webhook

    @requires_tier(
        LicenseTier.PRO,
        message=(
            "Budget alerts are a Pro feature — "
            "start a free trial: https://portal.tokenpak.io/trial"
        ),
    )
    def send(self, event: str, severity: str, message: str, **kwargs) -> bool:
        """POST Slack-shaped payload to the configured webhook URL.

        Returns True on success, False after all retries exhausted.
        Raises TierRequiredError if the active license is below PRO.
        """
        emoji = _SEVERITY_EMOJI.get(severity, "📢")
        text = f"{emoji} *[{severity.upper()}]* {message}"
        payload = {"text": text}

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.webhook,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    logger.debug(
                        "Slack delivered (attempt %d/%d): HTTP %d",
                        attempt,
                        _MAX_ATTEMPTS,
                        resp.status,
                    )
                    return True
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(2 ** (attempt - 1))  # 1 s, 2 s
                else:
                    logger.error(
                        "Slack delivery failed after %d attempts: %s",
                        _MAX_ATTEMPTS,
                        exc,
                    )
        return False
