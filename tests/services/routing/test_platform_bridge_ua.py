# SPDX-License-Identifier: Apache-2.0
"""Platform bridge — User-Agent detection (v1.3.15, 2026-04-24).

Added after verifying the installed OpenClaw binary at
``/home/sue/.nvm/.../openclaw/dist`` does NOT set an X-OpenClaw-Session
header on outbound LLM requests. Real detection fires on the
distinctive ``User-Agent: openclaw`` / ``OpenClaw-Gateway/1.0`` string.
Codex detection fires on ``Authorization: Bearer eyJ…`` (JWT prefix),
since the real Codex client carries a JWT access token but no other
distinctive header.
"""

from __future__ import annotations

from tokenpak.services.routing_service import platform_bridge as pb


# ── OpenClaw: User-Agent fallback ────────────────────────────────────


def test_openclaw_lowercase_user_agent_detects():
    origin = pb.detect_origin({"User-Agent": "openclaw"})
    assert origin is not None
    assert origin.platform_name == "openclaw"
    assert origin.session_id is None  # no session header → no mapping


def test_openclaw_gateway_user_agent_detects():
    origin = pb.detect_origin({"User-Agent": "OpenClaw-Gateway/1.0"})
    assert origin is not None
    assert origin.platform_name == "openclaw"


def test_openclaw_user_agent_is_case_insensitive():
    a = pb.detect_origin({"user-agent": "OPENCLAW"})
    b = pb.detect_origin({"User-Agent": "OpenClaw"})
    assert a is not None and b is not None
    assert a.platform_name == b.platform_name == "openclaw"


def test_openclaw_session_header_still_wins_for_session_id():
    origin = pb.detect_origin(
        {"User-Agent": "openclaw", "X-OpenClaw-Session": "sess-xyz"}
    )
    assert origin is not None
    assert origin.session_id == "sess-xyz"


def test_random_user_agent_doesnt_match_openclaw():
    for ua in ("curl/8", "python-requests/2.31", "anthropic-python/0.7", ""):
        origin = pb.detect_origin({"User-Agent": ua})
        if origin is not None:
            assert origin.platform_name != "openclaw", f"spurious match on UA={ua!r}"


# ── Codex: /v1/responses + JWT bearer ────────────────────────────────


def test_codex_bearer_jwt_detects():
    origin = pb.detect_origin(
        {"Authorization": "Bearer eyJfake_codex_jwt"}
    )
    assert origin is not None
    assert origin.platform_name == "codex"
    assert origin.declared_provider == "tokenpak-openai-codex"


def test_codex_ignores_non_jwt_bearer():
    """sk-* Bearer (OpenAI api-key) must not trigger Codex detection."""
    origin = pb.detect_origin({"Authorization": "Bearer sk-openai-abc123"})
    # Either None, or a non-codex platform — but NOT codex.
    if origin is not None:
        assert origin.platform_name != "codex"


def test_codex_ignores_missing_authorization():
    origin = pb.detect_origin({"Content-Type": "application/json"})
    if origin is not None:
        assert origin.platform_name != "codex"


# ── resolve_provider end-to-end ──────────────────────────────────────


def test_resolve_provider_openclaw_ua_defaults_to_claude_code():
    assert (
        pb.resolve_provider({"User-Agent": "openclaw"}) == "tokenpak-claude-code"
    )


def test_resolve_provider_codex_jwt_resolves_to_codex():
    assert (
        pb.resolve_provider({"Authorization": "Bearer eyJ_jwt_token"})
        == "tokenpak-openai-codex"
    )


def test_resolve_provider_explicit_header_beats_ua():
    assert (
        pb.resolve_provider(
            {"User-Agent": "openclaw", "X-TokenPak-Provider": "tokenpak-anthropic"}
        )
        == "tokenpak-anthropic"
    )
