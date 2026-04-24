# SPDX-License-Identifier: Apache-2.0
"""Platform bridge — unit + contract tests.

The bridge is the shared mechanism any agent platform (OpenClaw, Codex,
future) uses to tell tokenpak *"I am platform X, my session id is Y, and
I want provider Z"*. These tests pin:

  1. Registration is idempotent on name (no duplicate signals).
  2. Built-in OpenClaw signal fires on ``X-OpenClaw-Session``.
  3. Explicit ``X-TokenPak-Provider`` header beats the signal's default.
  4. ``resolve_provider`` returns the right provider name for common shapes.
  5. Third-party signals register cleanly and participate in detection.
  6. No OpenClaw marker + no explicit provider → ``None`` (legacy fallthrough).
"""

from __future__ import annotations

from tokenpak.services.routing_service import platform_bridge as pb


def _fresh_signal(name: str, marker_header: str, provider: str) -> pb.PlatformSignal:
    def _extract(headers):
        v = headers.get(marker_header.lower(), "").strip()
        if not v:
            return None
        return pb.PlatformOrigin(
            platform_name=name, session_id=v, declared_provider=None
        )

    return pb.PlatformSignal(name=name, default_provider=provider, extract=_extract)


# ── Registry basics ──────────────────────────────────────────────────────────


def test_openclaw_signal_is_built_in():
    names = {s.name for s in pb.registered()}
    assert "openclaw" in names


def test_register_is_idempotent_on_name():
    before = len(pb.registered())
    # Re-registering openclaw with a noop extract should not grow the list.
    pb.register(
        pb.PlatformSignal(
            name="openclaw",
            default_provider="tokenpak-claude-code",
            extract=lambda h: None,
        )
    )
    after = len(pb.registered())
    assert after == before


def test_register_then_detect_new_platform():
    # Restore openclaw at end so other tests keep working.
    from tokenpak.services.routing_service.platform_bridge import (
        _openclaw_signal as _real,
    )

    sig = _fresh_signal("fakeadapter", "X-Fake-Session", "tokenpak-fake")
    pb.register(sig)
    try:
        origin = pb.detect_origin({"X-Fake-Session": "abc-123"})
        assert origin is not None
        assert origin.platform_name == "fakeadapter"
        assert origin.session_id == "abc-123"
    finally:
        # Remove test signal by overwriting with a dead one.
        pb.register(
            pb.PlatformSignal(
                name="fakeadapter",
                default_provider="none",
                extract=lambda h: None,
            )
        )
        # Re-assert openclaw built-in is intact
        pb.register(_real)


# ── OpenClaw detection ───────────────────────────────────────────────────────


def test_detect_origin_openclaw_via_session_header():
    origin = pb.detect_origin({"X-OpenClaw-Session": "sess-777"})
    assert origin is not None
    assert origin.platform_name == "openclaw"
    assert origin.session_id == "sess-777"


def test_detect_origin_returns_none_for_unknown_traffic():
    origin = pb.detect_origin(
        {"User-Agent": "python-requests/2.31", "Content-Type": "application/json"}
    )
    assert origin is None


# ── Explicit provider header ─────────────────────────────────────────────────


def test_explicit_provider_header_overrides_signal_default():
    origin = pb.detect_origin(
        {
            "X-OpenClaw-Session": "sess-1",
            "X-TokenPak-Provider": "tokenpak-anthropic",
        }
    )
    assert origin is not None
    assert origin.declared_provider == "tokenpak-anthropic"


def test_read_declared_provider_returns_none_when_absent():
    assert pb.read_declared_provider({}) is None


def test_read_declared_provider_strips_whitespace():
    assert (
        pb.read_declared_provider({"X-TokenPak-Provider": "  tokenpak-claude-code  "})
        == "tokenpak-claude-code"
    )


# ── resolve_provider — end-to-end selector input ─────────────────────────────


def test_resolve_provider_explicit_header_wins():
    assert (
        pb.resolve_provider({"X-TokenPak-Provider": "tokenpak-anthropic"})
        == "tokenpak-anthropic"
    )


def test_resolve_provider_openclaw_default_is_claude_code():
    assert (
        pb.resolve_provider({"X-OpenClaw-Session": "x"}) == "tokenpak-claude-code"
    )


def test_resolve_provider_explicit_header_beats_openclaw_default():
    assert (
        pb.resolve_provider(
            {
                "X-OpenClaw-Session": "x",
                "X-TokenPak-Provider": "tokenpak-anthropic",
            }
        )
        == "tokenpak-anthropic"
    )


def test_resolve_provider_no_signal_returns_none():
    assert pb.resolve_provider({"User-Agent": "curl/8"}) is None


# ── Case-insensitivity ───────────────────────────────────────────────────────


def test_header_lookup_is_case_insensitive():
    a = pb.detect_origin({"x-openclaw-session": "aa"})
    b = pb.detect_origin({"X-OPENCLAW-SESSION": "aa"})
    c = pb.detect_origin({"X-OpenClaw-Session": "aa"})
    assert a and b and c
    assert a.session_id == b.session_id == c.session_id == "aa"
