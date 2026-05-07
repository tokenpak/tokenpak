# SPDX-License-Identifier: Apache-2.0
"""Tests for the feature-flagged creds-router injection helper.

We cover the contract that matters for live traffic safety:
* flag off → complete no-op (no mutation of fwd_headers)
* flag on but router declines → no-op (ambiguous, unknown tag, etc.)
* flag on and router succeeds → correct header shape per platform

The underlying router is tested separately; here we only verify the
proxy-side wiring.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tokenpak.creds import router
from tokenpak.creds.model import KIND_API_KEY, REFRESH_EXTERNAL, REFRESH_NONE, Credential
from tokenpak.creds.router import RouteDecision
from tokenpak.proxy import creds_injection

# ── helpers ──────────────────────────────────────────────────────────


def _flag_off():
    return patch.dict(os.environ, {"TOKENPAK_CREDS_ROUTER_ENABLED": "0"}, clear=False)


def _flag_on():
    return patch.dict(os.environ, {"TOKENPAK_CREDS_ROUTER_ENABLED": "1"}, clear=False)


def _fake_cred(
    cid: str = "fake-id",
    platform: str = "anthropic",
    kind: str = KIND_API_KEY,
) -> Credential:
    return Credential(
        id=cid,
        platform=platform,
        kind=kind,
        source="fake",
        provider="user-config",
        refresh_owner=REFRESH_NONE if kind == KIND_API_KEY else REFRESH_EXTERNAL,
        secret_ref=f"user-config:{cid}",
    )


# ── contract: flag off is a complete no-op ──────────────────────────


def test_flag_off_returns_false_and_no_mutation():
    with _flag_off():
        fwd = {"X-Foo": "bar"}
        result = creds_injection.maybe_inject(
            fwd, "https://api.anthropic.com/v1/messages", {}
        )
    assert result is False
    assert fwd == {"X-Foo": "bar"}


def test_flag_off_ignores_even_explicit_tag():
    """The flag must dominate — an explicit tag without the flag is not honoured."""
    with _flag_off():
        fwd = {}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/v1/messages",
            {"X-Tokenpak-Credential": "claude-max"},
        )
    assert result is False
    assert fwd == {}


# ── contract: router decline → no-op ────────────────────────────────


def test_unknown_explicit_tag_returns_false():
    with _flag_on():
        fwd = {}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/v1/messages",
            {"X-Tokenpak-Credential": "definitely-not-a-real-cred-id-12345"},
        )
    assert result is False
    assert fwd == {}


def test_router_exception_fails_open(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(creds_injection, "_get_header_ci", boom)
    with _flag_on():
        fwd = {"existing": "value"}
        result = creds_injection.maybe_inject(fwd, "https://api.anthropic.com/x", {})
    assert result is False
    assert fwd == {"existing": "value"}


# ── contract: successful injection produces correct header shape ────


@pytest.mark.parametrize(
    "platform,kind,expected_header,expected_stripped",
    [
        ("anthropic", KIND_API_KEY, "x-api-key", "Authorization"),
        ("anthropic", "oauth", "Authorization", "x-api-key"),
        ("openai", KIND_API_KEY, "Authorization", "x-api-key"),
        ("google", KIND_API_KEY, "Authorization", "x-api-key"),
        ("xai", KIND_API_KEY, "Authorization", "x-api-key"),
    ],
)
def test_platform_header_shapes(platform, kind, expected_header, expected_stripped):
    fwd = {"x-api-key": "old-value", "Authorization": "Bearer old"}
    creds_injection._inject_secret(fwd, platform, "SECRET", kind)
    assert expected_header in fwd
    assert expected_stripped not in fwd
    assert "SECRET" in fwd[expected_header]


def test_route_rule_match_injects_bearer_and_strips_old_auth(monkeypatch, tmp_path):
    rt = tmp_path / "routes.toml"
    rt.write_text(
        """
[[routes]]
callers = ["agent-alpha"]
destinations = ["api.anthropic.com"]
credential = "test-cred"
"""
    )
    monkeypatch.setattr(router, "ROUTES_PATH", rt)

    cred = _fake_cred("test-cred", platform="anthropic", kind=KIND_API_KEY)
    # ``router.select`` imports ``discover_all`` at module level, so we
    # patch the router-local binding, not the provider-module binding.
    monkeypatch.setattr(
        "tokenpak.creds.router.discover_all", lambda: [cred]
    )
    # ``maybe_inject`` re-imports ``resolve_secret`` inside the function,
    # so patching the provider-module binding is fine here.
    monkeypatch.setattr(
        "tokenpak.creds.providers.resolve_secret", lambda c: "TOP-SECRET"
    )

    with _flag_on():
        fwd = {"x-api-key": "client-supplied-stale"}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/v1/messages",
            {"X-Tokenpak-Caller": "agent-alpha"},
        )

    assert result is True
    assert fwd.get("x-api-key") == "TOP-SECRET"
    assert "Authorization" not in fwd  # we don't double-set for api_key kind


# ── policy: explicit-only override ──────────────────────────────────


def test_client_supplied_api_key_passes_through_without_hint(monkeypatch):
    """Client has its own key + no tokenpak header → router must not run."""
    # Guard: if the router ran we'd crash because this id doesn't exist.
    calls = {"count": 0}

    def _boom(_ctx):
        calls["count"] += 1
        raise AssertionError("router must not run when client brought own creds")

    monkeypatch.setattr("tokenpak.creds.router.select", _boom)
    with _flag_on():
        fwd = {"x-api-key": "client-provided"}
        result = creds_injection.maybe_inject(
            fwd, "https://api.anthropic.com/x", {"x-api-key": "sk-ant-real"}
        )
    assert result is False
    assert fwd == {"x-api-key": "client-provided"}
    assert calls["count"] == 0


def test_client_bearer_passes_through_without_hint(monkeypatch):
    monkeypatch.setattr(
        "tokenpak.creds.router.select",
        lambda _: (_ for _ in ()).throw(AssertionError("router ran")),
    )
    with _flag_on():
        fwd = {}
        result = creds_injection.maybe_inject(
            fwd, "https://api.openai.com/v1/chat", {"Authorization": "Bearer sk-real-key"}
        )
    assert result is False


def test_placeholder_credentials_do_not_block_router(monkeypatch):
    """OpenClaw-style placeholder auth should NOT count as real client creds."""
    cred = _fake_cred("fallback", platform="anthropic", kind=KIND_API_KEY)
    monkeypatch.setattr(
        "tokenpak.creds.router.select",
        lambda _ctx: RouteDecision(cred, "test", "platform-default"),
    )
    monkeypatch.setattr(
        "tokenpak.creds.providers.resolve_secret", lambda c: "REAL-SECRET"
    )

    with _flag_on():
        fwd = {"x-api-key": "custom-local"}  # the real-world OpenClaw placeholder
        result = creds_injection.maybe_inject(
            fwd, "https://api.anthropic.com/x", {"x-api-key": "custom-local"}
        )
    assert result is True
    assert fwd.get("x-api-key") == "REAL-SECRET"


def test_explicit_tag_overrides_client_creds(monkeypatch):
    """An X-Tokenpak-Credential header means 'take over' even with own key."""
    cred = _fake_cred("chosen", platform="anthropic", kind=KIND_API_KEY)
    monkeypatch.setattr("tokenpak.creds.router.discover_all", lambda: [cred])
    monkeypatch.setattr(
        "tokenpak.creds.providers.resolve_secret", lambda c: "FROM-ROUTER"
    )

    with _flag_on():
        fwd = {"x-api-key": "client-own-key"}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/x",
            {"x-api-key": "client-own-key", "X-Tokenpak-Credential": "chosen"},
        )
    assert result is True
    assert fwd.get("x-api-key") == "FROM-ROUTER"


def test_caller_hint_alone_lets_router_run(monkeypatch, tmp_path):
    """An X-Tokenpak-Caller header (rule-driven routing) also signals intent."""
    rt = tmp_path / "routes.toml"
    rt.write_text(
        """
[[routes]]
callers = ["agent-x"]
destinations = ["api.anthropic.com"]
credential = "rule-picked"
"""
    )
    monkeypatch.setattr(router, "ROUTES_PATH", rt)

    cred = _fake_cred("rule-picked", platform="anthropic", kind=KIND_API_KEY)
    monkeypatch.setattr("tokenpak.creds.router.discover_all", lambda: [cred])
    monkeypatch.setattr(
        "tokenpak.creds.providers.resolve_secret", lambda c: "PICKED"
    )

    with _flag_on():
        # Client has its own key AND sends X-Tokenpak-Caller → router runs.
        fwd = {"x-api-key": "client-key"}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/x",
            {"x-api-key": "client-key", "X-Tokenpak-Caller": "agent-x"},
        )
    assert result is True
    assert fwd.get("x-api-key") == "PICKED"


def test_unresolvable_secret_fails_open(monkeypatch):
    cred = _fake_cred("resolvable-id")

    def fake_decision(_ctx):
        return RouteDecision(cred, "test", "explicit")

    monkeypatch.setattr("tokenpak.creds.router.select", fake_decision)
    monkeypatch.setattr(
        "tokenpak.creds.providers.resolve_secret", lambda c: None
    )

    with _flag_on():
        fwd = {"Authorization": "Bearer orig"}
        result = creds_injection.maybe_inject(
            fwd,
            "https://api.anthropic.com/v1/messages",
            {"X-Tokenpak-Credential": "resolvable-id"},
        )

    assert result is False
    assert fwd == {"Authorization": "Bearer orig"}
