"""Canonical per-profile outbound header allowlist.

Single source of truth for which HTTP request headers the proxy may
forward upstream. Mirrors the ``SELF_CAPABILITIES_*`` pattern: this
module is the authoritative list; downstream code (conformance tests,
future header-filter stages) reads from here.

Header-name comparison is case-insensitive — ``tokenpak.proxy.passthrough``
already lowercases, and SC2-09 tests lowercase before membership check.

Phase TIP-SC+1 / SC2-03 (2026-04-22). Consumed by SC2-09's header-allowlist
invariant test suite.
"""
from __future__ import annotations


# Headers that every profile must strip before forwarding — hop-by-hop
# per RFC 7230 + Content-Encoding (because httpx auto-decompresses, so
# the upstream's Content-Encoding is a lie for the bytes we forward).
# The v1.2.6 zlib bug was forwarding Content-Encoding: gzip when the
# body was already decompressed — clients hit ZlibError on "decoded
# plaintext". HOP_BY_HOP encodes the lesson.
HOP_BY_HOP: frozenset[str] = frozenset({
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-length",
    "content-encoding",
    "proxy-connection",
    "upgrade",
    "te",
    "trailer",
})


# Headers the tip-proxy profile may forward upstream. Anything not here
# is either internal (X-TokenPak-*) or not the proxy's business.
#
# Rationale per entry:
# - host: required for virtual-hosted upstreams (Anthropic, OpenAI).
# - user-agent: forwarded so upstream can identify the client (CC CLI,
#   SDK, etc.); TokenPak does not rewrite it.
# - content-type: required for API routing.
# - accept, accept-encoding, accept-language: client preference
#   negotiation; harmless to forward.
# - authorization: primary auth for Bearer/OAuth providers.
# - x-api-key: Anthropic's primary API-key header.
# - anthropic-beta, anthropic-version, anthropic-dangerous-direct-*:
#   Anthropic API routing headers — must pass through verbatim for
#   cache/billing routing.
# - x-claude-code-*: Claude Code client markers; forwarded so upstream
#   OAuth path sees the authentic client identity.
# - x-request-id: client-side correlation; forwarded for end-to-end
#   tracing.
# - cache-control, if-match, if-none-match, if-modified-since: HTTP
#   caching semantics; harmless to forward.
# - date: client-declared request timestamp.
#
# NOT included (forbidden on forward):
# - cookie, set-cookie: stateful session material, should not cross
#   the proxy boundary.
# - x-tokenpak-*: proxy-internal metadata; forwarding would leak the
#   proxy's own state to the provider.
# - x-forwarded-*: client-ip/host metadata; tokenpak forwards on the
#   user's behalf from their local machine, so these are not
#   informative upstream.
PERMITTED_HEADERS_PROXY: frozenset[str] = frozenset({
    "host",
    "user-agent",
    "content-type",
    "accept",
    "accept-encoding",
    "accept-language",
    "authorization",
    "x-api-key",
    "anthropic-beta",
    "anthropic-version",
    "anthropic-dangerous-direct-browser-access",
    "x-claude-code-session-id",
    "x-claude-code-entrypoint",
    "x-request-id",
    "cache-control",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "date",
})


__all__ = ["HOP_BY_HOP", "PERMITTED_HEADERS_PROXY"]
