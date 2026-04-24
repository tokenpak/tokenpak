# SPDX-License-Identifier: Apache-2.0
"""credential_injector — unit tests (v1.3.15, 2026-04-24).

The injector translates a tokenpak provider slug into an
:class:`InjectionPlan` the proxy applies to the forward request. These
tests pin behavior for the two built-in providers (Claude CLI OAuth +
Codex ChatGPT OAuth) + the registration contract that lets third-party
adapters plug in.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tokenpak.services.routing_service import credential_injector as ci


@pytest.fixture(autouse=True)
def _clear_cache_per_test():
    """Kill the TTL cache before each test so earlier tests' plans don't
    bleed through. The cache is keyed on provider.name only; different
    file paths under the same name would otherwise hit the wrong cache
    entry across tests."""
    ci.invalidate_cache()
    yield
    ci.invalidate_cache()


# ── Registry basics ──────────────────────────────────────────────────


def test_builtins_are_registered():
    names = {p.name for p in ci.registered()}
    assert "tokenpak-claude-code" in names
    assert "tokenpak-openai-codex" in names


def test_register_is_idempotent_on_name():
    before = len(ci.registered())

    class _Dummy:
        name = "tokenpak-claude-code"

        def resolve(self):
            return None

    ci.register(_Dummy())
    after = len(ci.registered())
    assert after == before
    # Restore real Claude provider so the rest of the suite works.
    ci.register(ci.ClaudeCodeCredentialProvider())
    ci.invalidate_cache()


def test_resolve_unknown_provider_returns_none():
    ci.invalidate_cache()
    assert ci.resolve("not-a-real-provider") is None


# ── Claude OAuth provider ───────────────────────────────────────────


def _write_claude_creds(path: Path, access_token: str = "tok_" + "a" * 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": access_token,
                    "expiresAt": int((time.time() + 3600) * 1000),
                    "refreshToken": "rt_xxx",
                    "scopes": ["user:inference"],
                    "subscriptionType": "pro",
                    "rateLimitTier": "standard",
                }
            }
        )
    )


def test_claude_provider_resolves_to_full_claude_code_profile(tmp_path: Path):
    """v1.3.17: injector reproduces every header Claude CLI sends on
    the wire, not just the OAuth bearer + the one beta marker. Without
    the full profile (``claude-code-20250219`` beta + Claude CLI UA)
    Anthropic bills the traffic as generic API OAuth, not Claude Max
    Code — which exhausted the user's 'extra usage' pool on OpenClaw
    traffic while interactive ``tokenpak claude`` still worked."""
    creds = tmp_path / ".claude" / ".credentials.json"
    _write_claude_creds(creds, access_token="tok_xyz123")
    provider = ci.ClaudeCodeCredentialProvider(creds_path=creds)
    plan = provider.resolve()
    assert plan is not None
    # Bearer + every Claude Code identity marker present in add_headers.
    assert plan.add_headers["Authorization"] == "Bearer tok_xyz123"
    assert plan.add_headers["anthropic-dangerous-direct-browser-access"] == "true"
    assert plan.add_headers["x-app"] == "cli"
    assert plan.add_headers["User-Agent"].startswith("claude-cli/")
    # X-Claude-Code-Session-Id is CRITICAL — without it Anthropic
    # routes Claude Code OAuth to a restricted billing pool and
    # returns "out of extra usage" (verified end-to-end 2026-04-24).
    sess_id = plan.add_headers["X-Claude-Code-Session-Id"]
    # UUID4 shape: 8-4-4-4-12 hex chars.
    assert len(sess_id) == 36 and sess_id.count("-") == 4
    # anthropic-beta is in merge_headers (so it concats with caller's
    # feature-gate markers rather than clobbering them).
    beta = plan.merge_headers["anthropic-beta"]
    assert "claude-code-20250219" in beta, (
        "Missing claude-code beta marker — Anthropic won't treat "
        "this as Claude Code traffic"
    )
    assert "oauth-2025-04-20" in beta
    # Strip caller's auth + profile-clobbering headers so nothing
    # leaks through from OpenClaw's SDK defaults. anthropic-beta is
    # NOT stripped — it's merged with ours.
    assert "authorization" in plan.strip_headers
    assert "x-api-key" in plan.strip_headers
    assert "user-agent" in plan.strip_headers
    assert "x-app" in plan.strip_headers
    assert "anthropic-beta" not in plan.strip_headers  # merged, not stripped
    assert plan.target_url_override is None


def test_claude_provider_returns_none_when_file_missing(tmp_path: Path):
    provider = ci.ClaudeCodeCredentialProvider(
        creds_path=tmp_path / "nonexistent.json"
    )
    assert provider.resolve() is None


def test_claude_provider_returns_none_on_garbage_file(tmp_path: Path):
    creds = tmp_path / ".credentials.json"
    creds.write_text("this is not json")
    provider = ci.ClaudeCodeCredentialProvider(creds_path=creds)
    assert provider.resolve() is None


def test_claude_provider_returns_none_when_oauth_missing(tmp_path: Path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"wrongKey": "nope"}))
    provider = ci.ClaudeCodeCredentialProvider(creds_path=creds)
    assert provider.resolve() is None


def test_claude_provider_returns_none_on_empty_token(tmp_path: Path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": ""}}))
    provider = ci.ClaudeCodeCredentialProvider(creds_path=creds)
    assert provider.resolve() is None


# ── Codex OAuth provider ────────────────────────────────────────────


def _write_codex_creds(path: Path, access_token: str = "eyJ_codex", account: str = "acc-123") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access_token,
                    "account_id": account,
                    "id_token": "id_xxx",
                    "refresh_token": "rt_xxx",
                },
                "last_refresh": "2026-04-24T00:00:00Z",
            }
        )
    )


def test_codex_provider_resolves_with_chatgpt_headers_and_upstream(tmp_path: Path):
    creds = tmp_path / "auth.json"
    _write_codex_creds(creds, access_token="eyJ_CODEX_TOKEN", account="acc-foo")
    provider = ci.CodexCredentialProvider(creds_path=creds)
    plan = provider.resolve()
    assert plan is not None
    assert plan.add_headers["Authorization"] == "Bearer eyJ_CODEX_TOKEN"
    assert plan.add_headers["chatgpt-account-id"] == "acc-foo"
    assert plan.add_headers["originator"] == "codex_cli_rs"
    assert plan.add_headers["OpenAI-Beta"] == "responses=experimental"
    assert plan.target_url_override == "https://chatgpt.com/backend-api/codex/responses"
    assert plan.body_transform is not None


def test_codex_body_transform_enforces_payload_constraints(tmp_path: Path):
    creds = tmp_path / "auth.json"
    _write_codex_creds(creds)
    provider = ci.CodexCredentialProvider(creds_path=creds)
    plan = provider.resolve()
    assert plan is not None
    body = json.dumps(
        {"model": "gpt-5.3-codex", "messages": [{"role": "user", "content": "hi"}],
         "stream": False, "store": True, "max_output_tokens": 500}
    ).encode()
    out = plan.body_transform(body)
    parsed = json.loads(out)
    assert parsed["stream"] is True
    assert parsed["store"] is False
    assert "max_output_tokens" not in parsed
    assert parsed["model"] == "gpt-5.3-codex"  # preserved


def test_codex_body_transform_handles_non_json_gracefully(tmp_path: Path):
    creds = tmp_path / "auth.json"
    _write_codex_creds(creds)
    provider = ci.CodexCredentialProvider(creds_path=creds)
    plan = provider.resolve()
    assert plan is not None
    garbage = b"not json at all"
    assert plan.body_transform(garbage) == garbage


def test_codex_provider_returns_none_when_account_id_absent(tmp_path: Path):
    """Missing account_id: we still resolve, just skip the header
    (some deployments don't use it)."""
    creds = tmp_path / "auth.json"
    creds.write_text(
        json.dumps({"tokens": {"access_token": "eyJ_token"}})
    )
    provider = ci.CodexCredentialProvider(creds_path=creds)
    plan = provider.resolve()
    assert plan is not None
    assert "chatgpt-account-id" not in plan.add_headers


def test_codex_provider_returns_none_on_empty_token(tmp_path: Path):
    creds = tmp_path / "auth.json"
    creds.write_text(json.dumps({"tokens": {"access_token": ""}}))
    provider = ci.CodexCredentialProvider(creds_path=creds)
    assert provider.resolve() is None


# ── TTL cache ───────────────────────────────────────────────────────


def test_cache_returns_same_plan_across_calls(tmp_path: Path):
    creds = tmp_path / ".credentials.json"
    _write_claude_creds(creds, access_token="tok_v1")
    provider = ci.ClaudeCodeCredentialProvider(creds_path=creds)

    # Re-register so the hot-path `resolve(name)` hits THIS provider.
    ci.register(provider)
    ci.invalidate_cache()

    plan1 = ci.resolve("tokenpak-claude-code")
    assert plan1 is not None
    assert plan1.add_headers["Authorization"] == "Bearer tok_v1"

    # Rotate the file; without invalidation, the cached v1 plan persists.
    _write_claude_creds(creds, access_token="tok_v2")
    plan2 = ci.resolve("tokenpak-claude-code")
    assert plan2.add_headers["Authorization"] == "Bearer tok_v1"  # still cached

    ci.invalidate_cache()
    plan3 = ci.resolve("tokenpak-claude-code")
    assert plan3.add_headers["Authorization"] == "Bearer tok_v2"  # fresh read


# ── resolve() hot path ──────────────────────────────────────────────


def test_resolve_walks_registry_for_match(tmp_path: Path):
    creds = tmp_path / ".credentials.json"
    _write_claude_creds(creds, access_token="tok_rs")
    ci.register(ci.ClaudeCodeCredentialProvider(creds_path=creds))
    ci.invalidate_cache()
    plan = ci.resolve("tokenpak-claude-code")
    assert plan is not None
    assert "Bearer tok_rs" in plan.add_headers["Authorization"]


def test_resolve_third_party_provider_plugs_in_cleanly():
    class _ThirdParty:
        name = "tokenpak-fakeadapter"

        def resolve(self):
            return ci.InjectionPlan(
                add_headers={"X-Fake-Auth": "hello"}
            )

    ci.register(_ThirdParty())
    ci.invalidate_cache()
    plan = ci.resolve("tokenpak-fakeadapter")
    assert plan is not None
    assert plan.add_headers["X-Fake-Auth"] == "hello"
    # Remove from registry so later tests don't see it.
    ci.register(
        type("_Dead", (), {"name": "tokenpak-fakeadapter", "resolve": lambda self: None})()
    )
    ci.invalidate_cache()


# ── InjectionPlan immutability ──────────────────────────────────────


def test_injection_plan_is_hashable_frozen_dataclass():
    # frozen=True on the dataclass — assigning should raise.
    p = ci.InjectionPlan(add_headers={"x": "y"})
    with pytest.raises((AttributeError, Exception)):
        p.add_headers = {"evil": "value"}  # type: ignore[misc]
