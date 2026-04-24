# SPDX-License-Identifier: Apache-2.0
"""Platform bridge — X-TokenPak-Backend header routing (v1.3.16, 2026-04-24).

v1.3.15 shipped User-Agent detection for OpenClaw, but live HDR-DUMP
proved OpenClaw actually uses the Anthropic JS SDK (``User-Agent:
Anthropic/JS <ver>``) — not a distinctive UA. The real signal OpenClaw
carries is the ``X-TokenPak-Backend`` header, installed by
``tokenpak-inject.sh`` into every ``tokenpak-*`` provider entry in
``~/.openclaw/openclaw.json``. These tests pin the bridge reading that
header and mapping it to the correct provider.
"""

from __future__ import annotations

from tokenpak.services.routing_service import platform_bridge as pb


def test_x_tokenpak_backend_claude_code_maps_to_claude_provider():
    assert (
        pb.resolve_provider({"X-TokenPak-Backend": "claude-code"})
        == "tokenpak-claude-code"
    )


def test_x_tokenpak_backend_oauth_alias_also_maps_to_claude():
    assert (
        pb.resolve_provider({"X-TokenPak-Backend": "oauth"})
        == "tokenpak-claude-code"
    )


def test_x_tokenpak_backend_api_maps_to_anthropic_provider():
    assert (
        pb.resolve_provider({"X-TokenPak-Backend": "api"})
        == "tokenpak-anthropic"
    )


def test_x_tokenpak_backend_is_case_insensitive():
    for val in ("claude-code", "CLAUDE-CODE", "Claude-Code", " claude-code "):
        assert (
            pb.resolve_provider({"X-TokenPak-Backend": val}) == "tokenpak-claude-code"
        ), f"failed for {val!r}"


def test_header_name_is_case_insensitive():
    for key in ("X-TokenPak-Backend", "x-tokenpak-backend", "X-TOKENPAK-BACKEND"):
        assert (
            pb.resolve_provider({key: "claude-code"}) == "tokenpak-claude-code"
        ), f"failed for header {key!r}"


def test_unknown_backend_value_falls_through_to_signals():
    # Unknown value must NOT short-circuit; should fall through and
    # return None (no signal will match).
    assert pb.resolve_provider({"X-TokenPak-Backend": "something-else"}) is None


def test_x_tokenpak_provider_still_wins_over_backend_header():
    """Explicit X-TokenPak-Provider has highest precedence."""
    assert (
        pb.resolve_provider(
            {
                "X-TokenPak-Backend": "claude-code",
                "X-TokenPak-Provider": "tokenpak-anthropic",
            }
        )
        == "tokenpak-anthropic"
    )


def test_real_openclaw_shape_resolves_to_claude_code():
    """The actual headers OpenClaw sends: Anthropic JS SDK UA + x-api-key
    placeholder + X-TokenPak-Backend. Pre-fix: UA doesn't match openclaw
    pattern → falls through → None → proxy uses byte-preserve, which
    sends the fake x-api-key to Anthropic → 401. Post-fix: backend
    header → tokenpak-claude-code → credential injection fires."""
    headers = {
        "User-Agent": "Anthropic/JS 0.73.0",
        "anthropic-version": "2023-06-01",
        "x-api-key": "placeholder-key",
        "X-TokenPak-Backend": "claude-code",
        "anthropic-beta": "fine-grained-tool-streaming-2025-05-14,interleaved-thinking-2025-05-14",
    }
    assert pb.resolve_provider(headers) == "tokenpak-claude-code"
