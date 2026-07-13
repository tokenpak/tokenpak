"""Per-route HTTP header forwarding for the tokenpak proxy pipeline.

Extracted from proxy.py.

Each route classification maps to a header-forwarding strategy:
- ``forward_all``: relay every client header (Claude Code client-auth pass-through)
- ``allowlist``:   only forward headers on the route's allowlist (OpenClaw)
- ``sanitize``:    strip known-bad hop-by-hop / dangerous headers (SDK, non-Anthropic)

Whichever strategy builds the upstream header set, headers in the
TokenPak-internal namespace (``x-tokenpak-*`` / ``x-tpk-*``) are stripped
before the request leaves the proxy: they are internal plumbing and must
never reach a provider upstream. The allowlist strategies exclude them by
construction (no allowlist contains an internal name); the relay and
sanitize strategies strip them explicitly.
"""
from __future__ import annotations

from typing import Dict

from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW

# ---------------------------------------------------------------------------
# Header allowlists — mirrors proxy.py
# ---------------------------------------------------------------------------

# OPENCLAW_HEADER_ALLOWLIST must never gain new entries — OpenClaw traffic
# must produce exactly the same forwarded headers as before (bit-for-bit).
OPENCLAW_HEADER_ALLOWLIST: frozenset = frozenset((
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
))

# CLAUDE_CODE_HEADER_ALLOWLIST extends it with Claude Code-specific headers.
CLAUDE_CODE_HEADER_ALLOWLIST: frozenset = frozenset((
    "x-api-key",
    "authorization",
    "content-type",
    "anthropic-version",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    "x-claude-code-session-id",
    "user-agent",
    # Claude Code native headers — required for proper quota routing
    "accept",
    "x-app",
    "x-stainless-arch",
    "x-stainless-lang",
    "x-stainless-os",
    "x-stainless-package-version",
    "x-stainless-retry-count",
    "x-stainless-runtime",
    "x-stainless-runtime-version",
    "x-stainless-timeout",
))

# Headers that should never be forwarded (hop-by-hop + proxy internals).
_HOP_BY_HOP_HEADERS: frozenset = frozenset((
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "accept-encoding",
))

# Broader blocklist for the ``sanitize`` strategy (non-Anthropic providers).
BLOCKED_FORWARD_HEADERS: frozenset = frozenset((
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "accept-encoding",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
))


# ---------------------------------------------------------------------------
# Internal-namespace strip (final upstream forwarding boundary)
# ---------------------------------------------------------------------------

def _is_internal_header(name: str) -> bool:
    """True when *name* is a TokenPak-internal header (never forwarded).

    Delegates to the single canonical namespace predicate so the forwarding
    boundary and the traffic classifier can never disagree on the strip set.
    """
    from tokenpak.proxy.spend_guard.classifier import is_internal_header

    return is_internal_header(name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forward_headers(
    raw_headers: Dict[str, str],
    route: str,
    client_has_auth: bool = False,
) -> Dict[str, str]:
    """Build the forwarded-header dict for an outbound request.

    Args:
        raw_headers: Incoming request headers (key-value mapping from
            the HTTP handler or ``ProxyRequest.headers``).
        route: Route classification string — one of ``ROUTE_CLAUDE_CODE``,
            ``ROUTE_OPENCLAW``, ``ROUTE_SDK``, or any string.
        client_has_auth: True when the client supplied its own
            ``x-api-key`` or ``Authorization`` header.  Claude Code
            client-auth requests forward ALL headers (pure relay).

    Returns:
        A new header dict containing only the headers that should be
        forwarded to the upstream provider. TokenPak-internal headers
        (``x-tokenpak-*`` / ``x-tpk-*``) are never included, whichever
        strategy applies.
    """
    if route == ROUTE_CLAUDE_CODE and client_has_auth:
        # Client-auth pass-through: forward ALL headers (like a pure relay),
        # except hop-by-hop and TokenPak-internal headers.
        return {
            k: v for k, v in raw_headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS and not _is_internal_header(k)
        }

    if route == ROUTE_CLAUDE_CODE:
        # Allowlists never contain internal names, so the allowlist filter
        # already excludes the internal namespace; output is unchanged.
        return {
            k.lower(): v for k, v in raw_headers.items()
            if k.lower() in CLAUDE_CODE_HEADER_ALLOWLIST
        }

    if route == ROUTE_OPENCLAW:
        return {
            k.lower(): v for k, v in raw_headers.items()
            if k.lower() in OPENCLAW_HEADER_ALLOWLIST
        }

    # Default (SDK + unknown): sanitize — strip known-bad headers
    return sanitize_headers(raw_headers)


def sanitize_headers(raw_headers: Dict[str, str]) -> Dict[str, str]:
    """Strip hop-by-hop, dangerous, and TokenPak-internal headers (fallback strategy)."""
    return {
        k: v for k, v in raw_headers.items()
        if k.lower() not in BLOCKED_FORWARD_HEADERS and not _is_internal_header(k)
    }
