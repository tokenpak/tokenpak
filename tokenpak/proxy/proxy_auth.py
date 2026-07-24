"""
TokenPak Proxy-Level Authentication Gate
========================================

Opt-in middleware that enforces ``Authorization: Bearer <token>`` on
**non-localhost** requests when ``TOKENPAK_PROXY_AUTH_TOKEN`` is set in the
environment. Localhost (``127.0.0.1`` / ``::1``) is always trusted to preserve
the long-standing single-user developer workflow.

Decision tree
-------------

================== ============================== ============= ====================
client_ip          TOKENPAK_PROXY_AUTH_TOKEN      Authorization Decision
================== ============================== ============= ====================
localhost          (any)                           (any)         allow (mode=localhost)
non-localhost      unset                           (any)         403 (mode=forbidden)
non-localhost      set                             missing       401 (mode=missing)
non-localhost      set                             wrong         401 (mode=missing)
non-localhost      set                             correct       allow (mode=bearer)
================== ============================== ============= ====================

Bearer comparison uses :func:`hmac.compare_digest` to be timing-safe.

Identity
--------

When the gate allows a request via the Bearer path, the decision exposes
``user_id_hash`` — the lower-case hex SHA-256 of the supplied token. This is
the *only* identity that downstream telemetry (``telemetry-row.user_id``) ever
sees. The raw token is never logged, persisted, or copied to outbound headers.

I5 header-allowlist
-------------------

The Authorization header that carried the *proxy* auth token must NOT be
forwarded upstream. The upstream provider (Anthropic, OpenAI, Google, …) gets
its own credential from the request's ``x-api-key`` header or from a credential
the proxy injects server-side; the proxy auth Bearer token lives only between
the client and the proxy. :func:`strip_proxy_auth_for_upstream` enforces this
on the forwarding side.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_AUTH_ENV_VAR: str = "TOKENPAK_PROXY_AUTH_TOKEN"
"""Environment variable that, when set, activates the proxy-level auth gate."""

LOCAL_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})
"""Client addresses that bypass the auth gate (single-user dev workflow)."""

_DECISION_LOCALHOST = "localhost"
_DECISION_BEARER = "bearer"
_DECISION_MISSING = "missing"
_DECISION_FORBIDDEN = "forbidden"


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyAuthDecision:
    """Outcome of the proxy auth check.

    Attributes
    ----------
    allowed : bool
        True iff the request may proceed past the gate.
    status_code : int
        HTTP status code to return when ``allowed`` is False (200 otherwise).
    error_body : bytes
        JSON body to send when ``allowed`` is False (empty otherwise).
    user_id_hash : str | None
        SHA-256 hex of the proxy auth token when the Bearer path matched, else
        None. Downstream telemetry must use this — never the raw token.
    mode : str
        One of ``localhost``, ``bearer``, ``missing``, ``forbidden`` — useful
        for structured logging.
    """

    allowed: bool
    status_code: int
    error_body: bytes
    user_id_hash: Optional[str]
    mode: str


def _err(error_type: str, message: str) -> bytes:
    return json.dumps({"error": {"type": error_type, "message": message}}).encode("utf-8")


def _is_localhost(client_ip: str) -> bool:
    if not client_ip:
        return False
    ip = client_ip.strip().lower()
    if ip in LOCAL_IPS:
        return True
    # ::ffff:127.0.0.1 — IPv4-mapped IPv6 form some servers emit
    if ip.startswith("::ffff:") and ip.split("::ffff:", 1)[1] in LOCAL_IPS:
        return True
    return False


def _extract_authorization(headers: object) -> Optional[str]:
    """Return the Authorization header value (case-insensitive) or None."""
    if headers is None:
        return None
    if hasattr(headers, "items"):
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == "authorization" and isinstance(v, str):
                return v
    if hasattr(headers, "get"):
        for variant in ("Authorization", "authorization", "AUTHORIZATION"):
            v = headers.get(variant)
            if isinstance(v, str) and v:
                return v
    return None


def hash_token(token: str) -> str:
    """Return the lower-case hex SHA-256 digest of *token*.

    Used as the canonical identity for telemetry. Stable across restarts,
    one-way, and (with a high-entropy token) un-correlatable to the raw
    secret.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def check_proxy_auth(
    client_ip: str,
    headers: Any,
    env: Optional[Mapping[str, str]] = None,
) -> ProxyAuthDecision:
    """Evaluate the proxy-level auth gate for one incoming request.

    This is a pure function: no I/O, no side effects, no logging. Callers wire
    the decision into their HTTP server (see ``_ProxyHandler`` in
    ``proxy/server.py``).

    Parameters
    ----------
    client_ip : str
        Remote address of the TCP connection (``self.client_address[0]`` on
        ``BaseHTTPRequestHandler``).
    headers : Mapping
        The incoming request headers. Any object supporting ``.items()`` /
        ``.get()`` works (``http.client.HTTPMessage``, plain dict, etc.).
    env : Mapping[str, str], optional
        Override for ``os.environ`` — primarily for tests.
    """
    if env is None:
        env = os.environ

    if _is_localhost(client_ip):
        return ProxyAuthDecision(
            allowed=True,
            status_code=200,
            error_body=b"",
            user_id_hash=None,
            mode=_DECISION_LOCALHOST,
        )

    expected = env.get(PROXY_AUTH_ENV_VAR, "").strip()
    if not expected:
        return ProxyAuthDecision(
            allowed=False,
            status_code=403,
            error_body=_err(
                "forbidden",
                "non-localhost access requires TOKENPAK_PROXY_AUTH_TOKEN",
            ),
            user_id_hash=None,
            mode=_DECISION_FORBIDDEN,
        )

    auth_header = _extract_authorization(headers)
    if not auth_header:
        return ProxyAuthDecision(
            allowed=False,
            status_code=401,
            error_body=_err("unauthorized", "invalid or missing proxy auth token"),
            user_id_hash=None,
            mode=_DECISION_MISSING,
        )

    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return ProxyAuthDecision(
            allowed=False,
            status_code=401,
            error_body=_err("unauthorized", "invalid or missing proxy auth token"),
            user_id_hash=None,
            mode=_DECISION_MISSING,
        )

    supplied = parts[1].strip()
    if not hmac.compare_digest(supplied, expected):
        return ProxyAuthDecision(
            allowed=False,
            status_code=401,
            error_body=_err("unauthorized", "invalid or missing proxy auth token"),
            user_id_hash=None,
            mode=_DECISION_MISSING,
        )

    return ProxyAuthDecision(
        allowed=True,
        status_code=200,
        error_body=b"",
        user_id_hash=hash_token(supplied),
        mode=_DECISION_BEARER,
    )


# ---------------------------------------------------------------------------
# I5 — strip the proxy auth Bearer from upstream-bound headers
# ---------------------------------------------------------------------------


def strip_proxy_auth_for_upstream(
    fwd_headers: dict[str, str],
    client_authorization: Optional[str],
) -> dict[str, str]:
    """Remove the proxy auth Bearer token from headers about to leave the proxy.

    This protects against the I5 invariant — the Bearer token authenticating
    the *client to the proxy* must never leak to the upstream provider.

    Parameters
    ----------
    fwd_headers : dict
        Mutable headers dict that will be sent upstream. Modified in place.
    client_authorization : str | None
        The Authorization header value as it arrived from the client. When the
        proxy auth gate accepted the request via the Bearer path, this is the
        same value the gate matched against ``TOKENPAK_PROXY_AUTH_TOKEN``.

    Returns
    -------
    dict
        ``fwd_headers`` (same instance, returned for call-site convenience).

    Notes
    -----
    Only the *client-supplied* Authorization is stripped. Subsequent injection
    paths (``creds_router``, codex OAuth) may set their own ``Authorization``
    header; those are upstream credentials and remain.
    """
    if not client_authorization:
        return fwd_headers
    # Walk all case variants — fwd_headers may have been built with lower-cased
    # keys (Anthropic allowlist path) or original casing (legacy path).
    for variant in ("authorization", "Authorization", "AUTHORIZATION"):
        if variant in fwd_headers and fwd_headers[variant] == client_authorization:
            del fwd_headers[variant]
    return fwd_headers
