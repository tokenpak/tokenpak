# SPDX-License-Identifier: Apache-2.0
"""Phase 1 adapter pack: Mistral + Groq + Together + DeepSeek + OpenRouter.

Five OpenAI-Chat-compatible providers wired as CredentialProviders.
The OpenAIChatAdapter handles the wire format; what differs per
provider is the upstream URL + the API key (read from a per-provider
env var) + (OpenRouter only) two static headers.
"""

from __future__ import annotations

import pytest

from tokenpak.services.routing_service.credential_injector import (
    DeepSeekCredentialProvider,
    GroqCredentialProvider,
    MistralCredentialProvider,
    OpenRouterCredentialProvider,
    TogetherCredentialProvider,
    invalidate_cache,
    registered,
    resolve,
)

# ── Per-provider resolve() shape ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    """The credential injector caches resolutions for 30s — reset between
    tests so an env-var change in one test doesn't leak into the next."""
    invalidate_cache()
    yield
    invalidate_cache()


PROVIDERS = [
    (MistralCredentialProvider, "MISTRAL_API_KEY", "api.mistral.ai"),
    (GroqCredentialProvider, "GROQ_API_KEY", "api.groq.com"),
    (TogetherCredentialProvider, "TOGETHER_API_KEY", "api.together.xyz"),
    (DeepSeekCredentialProvider, "DEEPSEEK_API_KEY", "api.deepseek.com"),
    (OpenRouterCredentialProvider, "OPENROUTER_API_KEY", "openrouter.ai"),
]


@pytest.mark.parametrize("cls,env_var,host", PROVIDERS)
class TestEnvKeyBearerProviders:
    def test_no_env_var_returns_none(self, cls, env_var, host, monkeypatch):
        monkeypatch.delenv(env_var, raising=False)
        plan = cls().resolve()
        assert plan is None

    def test_empty_env_var_returns_none(self, cls, env_var, host, monkeypatch):
        monkeypatch.setenv(env_var, "")
        plan = cls().resolve()
        assert plan is None

    def test_whitespace_env_var_returns_none(self, cls, env_var, host, monkeypatch):
        monkeypatch.setenv(env_var, "   ")
        plan = cls().resolve()
        assert plan is None

    def test_valid_key_emits_plan_with_bearer_and_url(
        self, cls, env_var, host, monkeypatch
    ):
        monkeypatch.setenv(env_var, "sk-test-key-1234567890")
        plan = cls().resolve()
        assert plan is not None
        assert plan.target_url_override is not None
        assert host in plan.target_url_override
        assert plan.add_headers["Authorization"] == "Bearer sk-test-key-1234567890"
        # Caller's auth must be stripped before our auth lands.
        assert "authorization" in plan.strip_headers
        assert "x-api-key" in plan.strip_headers

    def test_target_url_override_uses_https(self, cls, env_var, host, monkeypatch):
        monkeypatch.setenv(env_var, "sk-test")
        plan = cls().resolve()
        assert plan.target_url_override.startswith("https://")


# ── OpenRouter-specific extra headers ─────────────────────────────────


class TestOpenRouterExtraHeaders:
    def test_emits_http_referer_and_x_title(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        plan = OpenRouterCredentialProvider().resolve()
        assert plan is not None
        assert plan.add_headers.get("HTTP-Referer") == "https://tokenpak.ai"
        assert plan.add_headers.get("X-Title") == "TokenPak"
        # Other providers don't have these.
        assert plan.add_headers["Authorization"] == "Bearer sk-or-test"

    def test_other_providers_do_not_have_referer_header(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-m-test")
        plan = MistralCredentialProvider().resolve()
        assert "HTTP-Referer" not in plan.add_headers
        assert "X-Title" not in plan.add_headers


# ── Registration: all 5 are auto-registered at import ─────────────────


class TestAutoRegistration:
    def test_all_five_providers_registered(self):
        names = {p.name for p in registered()}
        for slug in (
            "tokenpak-mistral",
            "tokenpak-groq",
            "tokenpak-together",
            "tokenpak-deepseek",
            "tokenpak-openrouter",
        ):
            assert slug in names, f"{slug!r} not auto-registered"

    def test_existing_providers_still_registered(self):
        names = {p.name for p in registered()}
        # Phase 1 must not have displaced the originals.
        assert "tokenpak-claude-code" in names
        assert "tokenpak-openai-codex" in names

    @pytest.mark.parametrize(
        "slug,env_var",
        [
            ("tokenpak-mistral", "MISTRAL_API_KEY"),
            ("tokenpak-groq", "GROQ_API_KEY"),
            ("tokenpak-together", "TOGETHER_API_KEY"),
            ("tokenpak-deepseek", "DEEPSEEK_API_KEY"),
            ("tokenpak-openrouter", "OPENROUTER_API_KEY"),
        ],
    )
    def test_resolve_by_slug_walks_registry(self, slug, env_var, monkeypatch):
        monkeypatch.setenv(env_var, "sk-resolve-test")
        plan = resolve(slug)
        assert plan is not None
        assert plan.add_headers["Authorization"] == "Bearer sk-resolve-test"


# ── Format-agnostic: each provider points at /v1/chat/completions ────


class TestUpstreamShapes:
    """All five upstreams target an OpenAI-Chat-Completions endpoint, so
    the existing OpenAIChatAdapter handles the wire format — no new
    format adapter required."""

    def test_all_target_chat_completions_endpoint(self, monkeypatch):
        for cls, env_var, _host in PROVIDERS:
            monkeypatch.setenv(env_var, "sk-test")
            plan = cls().resolve()
            invalidate_cache()
            assert "/chat/completions" in plan.target_url_override, (
                f"{cls.__name__} does not target a chat-completions endpoint"
            )
