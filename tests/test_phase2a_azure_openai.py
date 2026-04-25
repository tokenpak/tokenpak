# SPDX-License-Identifier: Apache-2.0
"""Phase 2a: Azure OpenAI adapter — body-aware URL resolution.

Azure OpenAI uses the same wire format as OpenAI Chat Completions but
routes by deployment id in the URL path
(``<endpoint>/openai/deployments/<dep>/chat/completions``) and uses
``api-key`` instead of ``Authorization: Bearer``. Tests verify:

  - The InjectionPlan's ``target_url_resolver`` is called with the
    request body + headers and returns a fully-qualified Azure URL.
  - Header override (``X-Azure-Deployment``) wins over body's model field.
  - Missing creds / endpoint / model field returns None at each layer.
  - ``api-key`` header replaces caller's auth.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.services.routing_service.credential_injector import (
    AzureOpenAICredentialProvider,
    invalidate_cache,
    registered,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _set_creds(monkeypatch, key="sk-azure-test", endpoint="https://my-resource.openai.azure.com"):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", key)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)


# ── resolve() returns plan only when both env vars set ────────────────


class TestResolveGate:
    def test_no_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        assert AzureOpenAICredentialProvider().resolve() is None

    def test_only_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        assert AzureOpenAICredentialProvider().resolve() is None

    def test_only_endpoint_returns_none(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
        assert AzureOpenAICredentialProvider().resolve() is None

    def test_both_set_returns_plan(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        assert plan is not None


# ── Auth shape: api-key header replaces caller auth ───────────────────


class TestAuthHeader:
    def test_emits_api_key_header_not_bearer(self, monkeypatch):
        _set_creds(monkeypatch, key="sk-real-azure-key")
        plan = AzureOpenAICredentialProvider().resolve()
        assert plan.add_headers == {"api-key": "sk-real-azure-key"}
        # Must NOT inject a Bearer Authorization (Azure doesn't accept it).
        assert "Authorization" not in plan.add_headers

    def test_strips_callers_auth_headers(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        assert "authorization" in plan.strip_headers
        assert "x-api-key" in plan.strip_headers


# ── URL resolution: body's model field → deployment id ────────────────


class TestUrlResolution:
    def test_model_field_becomes_deployment_in_url(self, monkeypatch):
        _set_creds(monkeypatch, endpoint="https://my-resource.openai.azure.com")
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "my-gpt4-deployment", "messages": []}).encode()
        url = plan.target_url_resolver(body, {})
        assert url == (
            "https://my-resource.openai.azure.com/openai/deployments/"
            "my-gpt4-deployment/chat/completions?api-version=2024-10-21"
        )

    def test_uses_explicit_api_version_env(self, monkeypatch):
        _set_creds(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-03-15-preview")
        # Recreate provider so env is reread (cache cleared by fixture).
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "x"}).encode()
        url = plan.target_url_resolver(body, {})
        assert "api-version=2025-03-15-preview" in url

    def test_endpoint_trailing_slash_tolerated(self, monkeypatch):
        _set_creds(monkeypatch, endpoint="https://x.openai.azure.com///")
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "d"}).encode()
        url = plan.target_url_resolver(body, {})
        # No double slashes after the host.
        assert url.startswith("https://x.openai.azure.com/openai/deployments/d/")
        assert "//openai" not in url

    def test_missing_model_field_returns_none(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"messages": []}).encode()
        assert plan.target_url_resolver(body, {}) is None

    def test_empty_model_field_returns_none(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "", "messages": []}).encode()
        assert plan.target_url_resolver(body, {}) is None

    def test_malformed_body_returns_none(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        assert plan.target_url_resolver(b"not json", {}) is None

    def test_empty_body_returns_none(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        assert plan.target_url_resolver(b"", {}) is None


class TestHeaderOverride:
    def test_x_azure_deployment_header_wins_over_body(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "body-deployment"}).encode()
        url = plan.target_url_resolver(
            body, {"X-Azure-Deployment": "header-deployment"}
        )
        assert "/deployments/header-deployment/" in url
        assert "body-deployment" not in url

    def test_lowercase_header_lookup(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "body-d"}).encode()
        url = plan.target_url_resolver(
            body, {"x-azure-deployment": "lower-d"}
        )
        assert "/deployments/lower-d/" in url

    def test_empty_header_falls_through_to_body(self, monkeypatch):
        _set_creds(monkeypatch)
        plan = AzureOpenAICredentialProvider().resolve()
        body = json.dumps({"model": "body-d"}).encode()
        url = plan.target_url_resolver(body, {"X-Azure-Deployment": "  "})
        assert "/deployments/body-d/" in url


# ── Auto-registration ────────────────────────────────────────────────


class TestRegistration:
    def test_registered_with_known_slug(self):
        names = {p.name for p in registered()}
        assert "tokenpak-azure-openai" in names

    def test_does_not_displace_existing_providers(self):
        names = {p.name for p in registered()}
        assert "tokenpak-claude-code" in names
        assert "tokenpak-mistral" in names
