# SPDX-License-Identifier: Apache-2.0
"""Phase 3: Cohere v2 (OpenAI-Chat-compat) + Vertex AI Gemini.

Cohere v2's chat endpoint mirrors OpenAI Chat Completions wire format,
so it slots into the Phase 1 ``_EnvKeyBearerProvider`` pattern as a
five-line subclass.

Vertex AI Gemini reuses the GoogleGenerativeAIAdapter for body shape
but routes by ``publishers/google/models/<id>:streamGenerateContent``
in the URL path and authenticates with OAuth2 access tokens fetched
via Application Default Credentials (google-auth).
"""

from __future__ import annotations

import json

import pytest

from tokenpak.services.routing_service.credential_injector import (
    CohereCredentialProvider,
    VertexAIGeminiCredentialProvider,
    invalidate_cache,
    registered,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


# ── Cohere v2 — Phase-1-style provider ───────────────────────────────


class TestCohere:
    def test_no_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        assert CohereCredentialProvider().resolve() is None

    def test_valid_key_emits_bearer_plan(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "co-test-1234567890")
        plan = CohereCredentialProvider().resolve()
        assert plan is not None
        assert plan.add_headers["Authorization"] == "Bearer co-test-1234567890"

    def test_targets_v2_chat_endpoint(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "co-test")
        plan = CohereCredentialProvider().resolve()
        assert plan.target_url_override == "https://api.cohere.ai/v2/chat"

    def test_strips_caller_auth_headers(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "co-test")
        plan = CohereCredentialProvider().resolve()
        assert "authorization" in plan.strip_headers
        assert "x-api-key" in plan.strip_headers


# ── Vertex AI Gemini ─────────────────────────────────────────────────


@pytest.fixture
def _vertex_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project-1234")
    monkeypatch.setenv("VERTEX_REGION", "us-central1")
    yield


class TestVertexGating:
    def test_returns_none_without_google_auth(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "google.auth", None)
        invalidate_cache()
        # Need the project too to isolate the boto3-style gate.
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        assert VertexAIGeminiCredentialProvider().resolve() is None

    def test_returns_none_without_project(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        assert VertexAIGeminiCredentialProvider().resolve() is None

    def test_returns_plan_when_both_present(self, monkeypatch, _vertex_env):
        # google-auth IS importable in this test env; project IS set.
        plan = VertexAIGeminiCredentialProvider().resolve()
        assert plan is not None

    def test_project_via_legacy_gcloud_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setenv("GCLOUD_PROJECT", "legacy-project-id")
        plan = VertexAIGeminiCredentialProvider().resolve()
        assert plan is not None
        # And the URL should reflect that project.
        body = json.dumps({"model": "gemini-pro", "contents": []}).encode()
        url = plan.target_url_resolver(body, {})
        assert "/projects/legacy-project-id/" in url


class TestVertexUrlResolution:
    def _resolver(self):
        return VertexAIGeminiCredentialProvider().resolve().target_url_resolver

    def test_non_streaming_picks_generateContent(self, _vertex_env):
        body = json.dumps({"model": "gemini-1.5-pro", "contents": []}).encode()
        url = self._resolver()(body, {})
        assert url == (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project-1234/locations/us-central1"
            "/publishers/google/models/gemini-1.5-pro:generateContent"
        )

    def test_streaming_picks_streamGenerateContent(self, _vertex_env):
        body = json.dumps({
            "model": "gemini-1.5-pro",
            "contents": [],
            "stream": True,
        }).encode()
        url = self._resolver()(body, {})
        assert url.endswith(
            "/publishers/google/models/gemini-1.5-pro:streamGenerateContent"
        )

    def test_region_env_override(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        monkeypatch.setenv("VERTEX_REGION", "europe-west4")
        body = json.dumps({"model": "gemini-pro", "contents": []}).encode()
        url = VertexAIGeminiCredentialProvider().resolve().target_url_resolver(
            body, {}
        )
        assert "europe-west4-aiplatform.googleapis.com" in url
        assert "/locations/europe-west4/" in url

    def test_default_region_when_unset(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        monkeypatch.delenv("VERTEX_REGION", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_REGION", raising=False)
        body = json.dumps({"model": "gemini-pro", "contents": []}).encode()
        url = VertexAIGeminiCredentialProvider().resolve().target_url_resolver(
            body, {}
        )
        assert "us-central1" in url

    def test_missing_model_returns_none(self, _vertex_env):
        body = json.dumps({"contents": []}).encode()
        assert self._resolver()(body, {}) is None


class TestVertexBodyTransform:
    def _xform(self, _vertex_env):
        return VertexAIGeminiCredentialProvider().resolve().body_transform

    def test_strips_model_field(self, _vertex_env):
        body = json.dumps({
            "model": "gemini-1.5-pro",
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        }).encode()
        out = self._xform(_vertex_env)(body)
        decoded = json.loads(out)
        assert "model" not in decoded
        assert decoded["contents"] == [
            {"role": "user", "parts": [{"text": "hi"}]}
        ]

    def test_strips_stream_field(self, _vertex_env):
        # Vertex encodes stream-vs-non-stream via the URL verb suffix,
        # NOT a body field. Strip stream from body to avoid the
        # backend rejecting it as an unknown field.
        body = json.dumps({
            "model": "gemini-pro",
            "stream": True,
            "contents": [],
        }).encode()
        out = self._xform(_vertex_env)(body)
        decoded = json.loads(out)
        assert "stream" not in decoded

    def test_preserves_generation_config(self, _vertex_env):
        body = json.dumps({
            "model": "gemini-pro",
            "contents": [],
            "generationConfig": {"maxOutputTokens": 100, "temperature": 0.5},
        }).encode()
        out = self._xform(_vertex_env)(body)
        decoded = json.loads(out)
        assert decoded["generationConfig"] == {
            "maxOutputTokens": 100,
            "temperature": 0.5,
        }


class TestVertexHeaderResolver:
    def test_emits_bearer_token_when_creds_resolvable(self, _vertex_env, monkeypatch):
        # Mock google.auth.default so we don't need real GCP creds.
        from unittest.mock import MagicMock

        fake_creds = MagicMock()
        fake_creds.valid = True
        fake_creds.token = "ya29.fake-access-token"

        import google.auth as _ga
        monkeypatch.setattr(
            _ga, "default", lambda scopes=None: (fake_creds, "my-project-1234")
        )

        plan = VertexAIGeminiCredentialProvider().resolve()
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/p/"
            "locations/us-central1/publishers/google/models/gemini:generateContent"
        )
        headers = plan.header_resolver(b'{"contents": []}', url, "POST", {})
        assert headers["Authorization"] == "Bearer ya29.fake-access-token"

    def test_returns_empty_dict_on_creds_failure(self, _vertex_env, monkeypatch):
        import google.auth as _ga

        def _explode(scopes=None):
            raise RuntimeError("no creds discoverable")

        monkeypatch.setattr(_ga, "default", _explode)
        plan = VertexAIGeminiCredentialProvider().resolve()
        headers = plan.header_resolver(b"{}", "https://x", "POST", {})
        assert headers == {}


# ── Auto-registration ────────────────────────────────────────────────


class TestRegistration:
    def test_both_registered(self):
        names = {p.name for p in registered()}
        assert "tokenpak-cohere" in names
        assert "tokenpak-vertex-gemini" in names

    def test_existing_providers_intact(self):
        names = {p.name for p in registered()}
        for slug in (
            "tokenpak-claude-code",
            "tokenpak-mistral",
            "tokenpak-azure-openai",
            "tokenpak-bedrock-claude",
        ):
            assert slug in names, f"Phase 3 displaced {slug!r}"
