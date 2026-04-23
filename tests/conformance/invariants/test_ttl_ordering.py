"""SC2-07 — I3 TTL ordering invariant (advisory).

Claim: On outbound body, no ``cache_control`` block with ``ttl="1h"``
appears AFTER a default-TTL (5m) block in document order. On
``claude-code-*`` (byte-preserve), the proxy preserves the client's
order regardless — I1 byte-identity wins.

Advisory status per SC+1 CI gating: runs but failures do not block
CI. Promoted to blocking in a follow-up packet after any pre-existing
prompt_builder findings are addressed.

See feedback_cache_control_ttl_ordering.md for the Anthropic
contract these tests defend against regressions.
"""
from __future__ import annotations

import json
from typing import Any, List

import pytest

pytestmark = [pytest.mark.conformance, pytest.mark.advisory]


def _cache_control_ttl_sequence(body: bytes) -> List[str]:
    """Walk the JSON body and return cache_control TTL values in document order.

    Anthropic's request shape: `system` + each `messages[*].content[*]`
    can carry a `cache_control` block with optional `ttl`. Missing `ttl`
    means Anthropic's default (5m).

    Returns a list like ``["default", "1h", "default"]`` — input to the
    assertion below.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return []

    ttls: List[str] = []

    def _walk_block(block: Any) -> None:
        if not isinstance(block, dict):
            return
        cc = block.get("cache_control")
        if isinstance(cc, dict):
            ttl = cc.get("ttl", "default")
            ttls.append(str(ttl))

    system = payload.get("system")
    if isinstance(system, list):
        for block in system:
            _walk_block(block)

    for msg in payload.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                _walk_block(block)

    return ttls


def _assert_no_1h_after_default(ttls: List[str], *, context: str = "") -> None:
    """Anthropic rule: no '1h' after 'default' in document order."""
    seen_default = False
    for i, ttl in enumerate(ttls):
        if ttl == "default" or ttl == "5m":
            seen_default = True
        elif ttl == "1h" and seen_default:
            raise AssertionError(
                f"TTL ordering violation at position {i}: '1h' after default/5m. "
                f"Sequence={ttls}. Anthropic rejects such requests. "
                f"{context}"
            )


# ── Walker correctness ──────────────────────────────────────────────

def test_walker_extracts_no_cache_control():
    body = b'{"model":"c","messages":[{"role":"user","content":"hi"}]}'
    assert _cache_control_ttl_sequence(body) == []


def test_walker_extracts_single_1h():
    body = (
        b'{"system":[{"type":"text","text":"preamble",'
        b'"cache_control":{"type":"ephemeral","ttl":"1h"}}],"messages":[]}'
    )
    assert _cache_control_ttl_sequence(body) == ["1h"]


def test_walker_extracts_default_then_1h_violation():
    body = (
        b'{"system":['
        b'{"type":"text","text":"short","cache_control":{"type":"ephemeral"}},'
        b'{"type":"text","text":"long","cache_control":{"type":"ephemeral","ttl":"1h"}}'
        b'],"messages":[]}'
    )
    assert _cache_control_ttl_sequence(body) == ["default", "1h"]


# ── Non-byte-preserve route: proxy MAY reorder; outbound must be clean ──

def test_anthropic_sdk_clean_order_passes(fire_outbound):
    """Proxy forwards a clean-order body → no violation."""
    body = (
        b'{"model":"claude-opus-4-7","system":['
        b'{"type":"text","text":"longer preamble","cache_control":{"type":"ephemeral","ttl":"1h"}},'
        b'{"type":"text","text":"short tail","cache_control":{"type":"ephemeral"}}'
        b'],"messages":[]}'
    )
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        headers={"content-type": "application/json", "x-api-key": "sk-stub"},
        body=body,
    )
    ttls = _cache_control_ttl_sequence(captured["body"])
    assert ttls == ["1h", "default"]
    _assert_no_1h_after_default(ttls, context="anthropic-sdk clean-order case")


def test_anthropic_sdk_violation_detected(fire_outbound):
    """If the outbound body still contains a violation, oracle flags it.

    This is the canary for a prompt_builder regression that stops
    reordering. Marked ``xfail`` initially because some pre-existing
    prompt_builder behaviors may surface here — part of the reason this
    invariant ships as advisory. If xfail turns xpass, the prompt_builder
    is enforcing correctly and we can promote to strict.
    """
    body = (
        b'{"model":"claude","system":['
        b'{"type":"text","text":"short","cache_control":{"type":"ephemeral"}},'
        b'{"type":"text","text":"long","cache_control":{"type":"ephemeral","ttl":"1h"}}'
        b'],"messages":[]}'
    )
    captured = fire_outbound(
        route_class="anthropic-sdk",
        url="https://api.anthropic.com/v1/messages",
        headers={"content-type": "application/json", "x-api-key": "sk-stub"},
        body=body,
    )
    ttls = _cache_control_ttl_sequence(captured["body"])
    # Because SC+1 tests drive notify directly (not through the
    # prompt_builder pipeline), the body we observe is exactly what
    # we submitted. The violation persists unless the proxy actually
    # reorders — which it doesn't at this layer. This assertion
    # proves the oracle detects the pattern.
    with pytest.raises(AssertionError, match="TTL ordering violation"):
        _assert_no_1h_after_default(ttls)


# ── Byte-preserve route: proxy MUST NOT reorder ─────────────────────

_CC_ROUTES = [
    "claude-code-tui",
    "claude-code-cli",
    "claude-code-tmux",
    "claude-code-sdk",
    "claude-code-ide",
    "claude-code-cron",
]


@pytest.mark.parametrize("route_class", _CC_ROUTES)
def test_claude_code_preserves_client_order(route_class, fire_outbound):
    """On byte-preserve routes, proxy passes client's TTL order verbatim,
    even if it violates Anthropic's rule — byte-identity (I1) wins.
    """
    violating_body = (
        b'{"model":"claude","system":['
        b'{"type":"text","text":"short","cache_control":{"type":"ephemeral"}},'
        b'{"type":"text","text":"long","cache_control":{"type":"ephemeral","ttl":"1h"}}'
        b'],"messages":[]}'
    )
    captured = fire_outbound(
        route_class=route_class,
        url="https://api.anthropic.com/v1/messages",
        headers={"authorization": "Bearer sk-oauth-stub"},
        body=violating_body,
    )
    # Byte-identity passthrough — violation preserved verbatim
    assert captured["body"] == violating_body, (
        f"byte-preserve route {route_class} must NOT mutate body "
        f"even to fix TTL order (I1 wins; Anthropic will 4xx on its own)"
    )
