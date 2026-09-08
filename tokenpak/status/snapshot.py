# SPDX-License-Identifier: Apache-2.0
"""One bounded, explicit-session reader; never forwards a provider request."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tokenpak.core.contracts.session_economics import SessionEconomics
from tokenpak.status.binding import valid_session

MAX_BYTES = 65536
MAX_AGE = 10


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(base: str) -> str:
    parsed = urlsplit(base)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/", "/v1")
    ):
        raise ValueError("status requires a loopback HTTP proxy")
    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("status requires a loopback HTTP proxy")
    return f"http://{parsed.netloc}/v1/messages/session-economics"


@dataclass(frozen=True)
class StatusSnapshot:
    session_id: str
    generated_at: float
    routing_mode: str = "unknown"
    economics: SessionEconomics | None = None
    reason: str = "unavailable"

    def to_dict(self) -> dict:
        observed = self.economics
        source = "monitor_db" if observed is not None else "unknown"
        stamp = {"source": source, "generated_at": self.generated_at, "stale": False}
        return {
            "schema_version": "tokenpak.status.snapshot/1",
            "session_id": self.session_id,
            "generated_at": self.generated_at,
            "expires_at": self.generated_at + MAX_AGE,
            "routing_mode": self.routing_mode,
            "session_cost": {
                **stamp,
                "value": observed.to_dict()["facts"]["cost_usd"] if observed else None,
            },
            "local_observed_spend": {**stamp, "source": "unknown", "value": None},
            "session_budget": {
                "source": "unknown",
                "generated_at": self.generated_at,
                "stale": False,
                "value": None,
            },
            "session_economics": observed.to_dict() if observed else None,
            "reason": self.reason,
        }


def fetch_snapshot(
    session_id: str,
    proxy: str,
    *,
    routing_mode: str = "unknown",
    now: datetime | None = None,
    timeout: float = 0.5,
) -> StatusSnapshot:
    now = now or datetime.now(timezone.utc)
    missing = StatusSnapshot(session_id, now.timestamp(), routing_mode)
    if not valid_session(session_id):
        return StatusSnapshot("", now.timestamp(), routing_mode, reason="waiting for session")
    try:
        req = Request(
            local_url(proxy),
            data=json.dumps({"session_id": session_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Never use a caller's HTTP proxy or follow a local redirect to a remote host.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return missing
        economics = SessionEconomics.from_dict(json.loads(raw))
        if economics.session.id != session_id:
            return StatusSnapshot(
                session_id, now.timestamp(), routing_mode, reason="session mismatch"
            )
        stamp = datetime.fromisoformat(economics.as_of.replace("Z", "+00:00"))
        if stamp.tzinfo is None or not -2 <= (now - stamp).total_seconds() <= MAX_AGE:
            return StatusSnapshot(session_id, now.timestamp(), routing_mode, reason="stale")
        return StatusSnapshot(session_id, now.timestamp(), routing_mode, economics, "")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return missing
