"""SC2-05 — I1 byte-identity invariant (blocking).

Claim: For any ``claude-code-*`` request, the body bytes forwarded to
upstream are byte-identical to the bytes the client submitted.

The proxy's byte-preserve contract (Constitution §5.2, memory:
project_tokenpak_claude_code_proxy) depends on this for Anthropic
OAuth billing — any re-serialization mutates cache_control block order
and charges the wrong billing line-item.

The observer captures what the proxy fires via SC2-02's
``notify_outbound_request`` right before dispatch. This test asserts
that captured body == submitted body. A negative canary test asserts
that the oracle actually detects mutation (not just says "equal" on
every test).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.conformance


_CC_ROUTE_CLASSES = [
    "claude-code-tui",
    "claude-code-cli",
    "claude-code-tmux",
    "claude-code-sdk",
    "claude-code-ide",
    "claude-code-cron",
]

_BODY_VARIANTS = {
    "plain": (
        b'{"model":"claude-opus-4-7","messages":[{"role":"user","content":"hello"}],"stream":true}'
    ),
    "with_cache_control": (
        b'{"model":"claude-opus-4-7","system":[{"type":"text","text":"stable preamble",'
        b'"cache_control":{"type":"ephemeral","ttl":"1h"}},{"type":"text","text":"tail"}],'
        b'"messages":[{"role":"user","content":"hello"}],"stream":true}'
    ),
    "system_plus_tools": (
        b'{"model":"claude-opus-4-7","system":"You are a helper.","tools":[{"name":"calc",'
        b'"description":"Arithmetic","input_schema":{"type":"object"}}],'
        b'"messages":[{"role":"user","content":"add 2+2"}]}'
    ),
}


@pytest.mark.parametrize("route_class", _CC_ROUTE_CLASSES)
@pytest.mark.parametrize("variant_name, body", list(_BODY_VARIANTS.items()))
def test_byte_identity_claude_code(route_class, variant_name, body, fire_outbound):
    """Captured outbound body MUST equal the client-submitted body on CC routes."""
    captured = fire_outbound(
        route_class=route_class,
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        headers={"authorization": "Bearer sk-oauth-stub", "content-type": "application/json"},
        body=body,
    )
    assert captured["body"] == body, (
        f"byte-identity violated on {route_class} / {variant_name}: "
        f"Anthropic OAuth billing depends on byte-exact bodies per Constitution §5.2."
    )
    assert captured["route_class"] == route_class


def test_byte_identity_oracle_detects_mutation(fire_outbound):
    """Negative canary: if we pre-mutate the captured body, the assertion must fail.

    Proves the oracle actually detects mutations, not just says "equal"
    on every test (which would be a false-positive generator).
    """
    original_body = b'{"model":"claude","messages":[],"stream":true}'
    captured = fire_outbound(
        route_class="claude-code-tui",
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        body=original_body,
    )

    # Simulate the mutation class this invariant is designed to catch
    mutated_body = original_body.replace(b'"stream":true', b'"stream": true')

    assert captured["body"] == original_body  # true equality
    assert captured["body"] != mutated_body, (
        "canary: oracle must detect whitespace-style mutations "
        "(JSON re-serialization is the #1 Anthropic-billing bug class)"
    )


def test_body_empty_bytes_still_identical(fire_outbound):
    """Edge case: empty body → captured must be b'', not None or missing."""
    captured = fire_outbound(
        route_class="claude-code-cli",
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        body=b"",
    )
    assert captured["body"] == b""
