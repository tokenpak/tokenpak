"""Upstream forwarding boundary: internal headers must never leave the proxy.

Negative integration tests over every header-forwarding strategy the proxy
uses to build the upstream request:

- ``forward_all``  — client-auth pure relay (Claude Code client-auth route)
- ``allowlist``    — Claude Code allowlist, OpenClaw allowlist, legacy allowlist
- ``sanitize``     — SDK / unknown / custom-provider fallback

Whatever the route or provider, headers in the TokenPak-internal namespace
(``x-tokenpak-*`` / ``x-tpk-*``) must be absent from the upstream header set.
The OpenClaw allowlist route must additionally remain byte-for-byte identical
to its historical output: its allowlist never contained internal names, so
the no-forward rule must not change that route's output at all.

Fixture policy: agent-name fixture values are neutral placeholder
identifiers, never real deployment or operator names. All tests are offline
and deterministic — no live network, no live provider.
"""

import pytest

from tokenpak.proxy.headers import (
    CLAUDE_CODE_HEADER_ALLOWLIST,
    OPENCLAW_HEADER_ALLOWLIST,
    forward_headers,
    sanitize_headers,
)
from tokenpak.proxy.passthrough import LEGACY_HEADER_ALLOWLIST
from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK
from tokenpak.proxy.spend_guard.classifier import is_internal_header, strip_managed_headers

# Internal-namespace headers as a client might (incorrectly) send them:
# mixed case, marker headers, and arbitrary internal-namespace names.
INTERNAL_HEADERS = {
    "X-Tokenpak-Managed": "1",
    "X-Tokenpak-Agent": "agent-a",
    "X-TOKENPAK-MANAGED-ENV": "1",
    "x-tokenpak-bypass": "1",
    "X-Tpk-Trace-Id": "trace-1",
}

# Representative legitimate client headers across the routed providers.
BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "x-api-key": "test-key",
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "prompt-caching-2024-07-31",
    "User-Agent": "client/1.0",
    "Accept": "application/json",
}


def _request_headers():
    headers = dict(BASE_HEADERS)
    headers.update(INTERNAL_HEADERS)
    return headers


def _internal_names_in(result):
    return sorted(k for k in result if is_internal_header(k))


# ---------------------------------------------------------------------------
# Route / strategy matrix — no internal header may survive any of them
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("route", "client_has_auth"),
    [
        pytest.param(ROUTE_CLAUDE_CODE, True, id="claude-client-auth-relay"),
        pytest.param(ROUTE_CLAUDE_CODE, False, id="claude-code-allowlist"),
        pytest.param(ROUTE_OPENCLAW, False, id="openclaw-allowlist"),
        pytest.param(ROUTE_SDK, False, id="sdk-sanitize"),
        pytest.param("custom-provider", False, id="custom-provider-sanitize"),
        pytest.param("", False, id="unknown-route-sanitize"),
    ],
)
def test_no_internal_header_reaches_upstream_on_any_route(route, client_has_auth):
    result = forward_headers(_request_headers(), route, client_has_auth=client_has_auth)
    assert _internal_names_in(result) == [], (
        f"internal headers leaked upstream on route {route!r}: {_internal_names_in(result)}"
    )


def test_sanitize_headers_strips_internal_namespace_directly():
    result = sanitize_headers(_request_headers())
    assert _internal_names_in(result) == []
    # Legitimate client headers survive the sanitize strategy.
    for name in ("Authorization", "x-api-key", "Content-Type", "User-Agent"):
        assert name in result


def test_client_auth_relay_still_forwards_client_headers():
    result = forward_headers(_request_headers(), ROUTE_CLAUDE_CODE, client_has_auth=True)
    assert _internal_names_in(result) == []
    # The relay behavior for legitimate headers is unchanged.
    for name in ("Authorization", "x-api-key", "anthropic-version", "User-Agent"):
        assert name in result


def test_openclaw_route_output_is_bit_for_bit_unchanged():
    """The OpenClaw allowlist never contained internal names; the no-forward
    rule must therefore not alter that route's output in any way."""
    raw = _request_headers()
    expected = {
        k.lower(): v for k, v in raw.items() if k.lower() in OPENCLAW_HEADER_ALLOWLIST
    }
    assert forward_headers(raw, ROUTE_OPENCLAW) == expected
    assert _internal_names_in(expected) == []


# ---------------------------------------------------------------------------
# Allowlist invariants — no allowlist may ever gain an internal name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("allowlist_name", "allowlist"),
    [
        ("OPENCLAW_HEADER_ALLOWLIST", OPENCLAW_HEADER_ALLOWLIST),
        ("CLAUDE_CODE_HEADER_ALLOWLIST", CLAUDE_CODE_HEADER_ALLOWLIST),
        ("LEGACY_HEADER_ALLOWLIST", frozenset(LEGACY_HEADER_ALLOWLIST)),
    ],
)
def test_allowlists_contain_no_internal_namespace_names(allowlist_name, allowlist):
    leaked = sorted(name for name in allowlist if is_internal_header(name))
    assert leaked == [], f"{allowlist_name} must never contain internal names: {leaked}"


# ---------------------------------------------------------------------------
# Alignment — the forwarding strip set and the classifier helper agree
# ---------------------------------------------------------------------------

def test_forwarding_strip_set_aligns_with_classifier_helper():
    """The strip helper and the forwarding boundary share one namespace
    predicate; both must remove exactly the internal fixture headers."""
    headers = _request_headers()
    removed = strip_managed_headers(headers)
    assert {name.lower() for name in removed} == {name.lower() for name in INTERNAL_HEADERS}
    # After the strip, nothing internal remains and client headers are intact.
    assert _internal_names_in(headers) == []
    assert headers == BASE_HEADERS
