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
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.proxy import creds_injection
from tokenpak.creds import router
from tokenpak.creds.model import Credential, REFRESH_EXTERNAL, REFRESH_NONE, KIND_API_KEY
from tokenpak.creds.router import RouteContext, RouteDecision


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
