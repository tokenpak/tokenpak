"""Webhook alert delivery channel.

POSTs a generic JSON payload to a configured URL with 3-attempt
exponential-backoff retry (1 s, 2 s, drop).  Timeout: 5 s per attempt.

Budget alerts are a Pro feature — gated via @requires_tier.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from tokenpak.license.gates import requires_tier
from tokenpak.license.tier import LicenseTier

logger = logging.getLogger(__name__)

_TIMEOUT = 5
_MAX_ATTEMPTS = 3


class WebhookChannel:
    """Delivers alerts as generic JSON POST requests."""

    def __init__(self, url: str) -> None:
        self.url = url

    @requires_tier(
        LicenseTier.PRO,
        message=(
            "Budget alerts are a Pro feature — "
            "start a free trial: https://portal.tokenpak.io/trial"
        ),
    )
    def send(self, event: str, severity: str, message: str, **kwargs) -> bool:
        """POST alert payload to the configured URL.

        Returns True on success, False after all retries exhausted.
        Raises TierRequiredError if the active license is below PRO.
        """
        payload: dict = {
            "event": event,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(kwargs)

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    logger.debug(
                        "Webhook delivered (attempt %d/%d): HTTP %d",
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
                        "Webhook delivery failed after %d attempts: %s",
                        _MAX_ATTEMPTS,
                        exc,
                    )
        return False
