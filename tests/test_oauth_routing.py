"""
Tests for OAuth routing support — AC3: test Codex OAuth + auth detection.

Covers:
- detect_auth_type() distinguishes API keys vs OAuth Bearer tokens
- detect_token_format() identifies JWT, opaque, apikey shapes
- analyze_request() correctly classifies Codex, Claude Code OAuth, and API key requests
- ProviderRouter routes Codex models to openai-codex endpoint
- ProviderRouter routes /v1/responses to openai-codex
- OAuth contexts flag skip_cache_keying=True
- API key contexts flag skip_cache_keying=False
- oauth_telemetry_tags() returns no credential material
"""

from __future__ import annotations

import json

from tokenpak.proxy.oauth import (
    AUTH_TYPE_APIKEY,
    AUTH_TYPE_NONE,
    AUTH_TYPE_OAUTH,
    analyze_request,
    detect_auth_type,
    detect_token_format,
    is_codex_model,
    oauth_telemetry_tags,
)
from tokenpak.proxy.router import ProviderRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = "sk-ant-api03-testkey123"
OPENAI_API_KEY = "sk-testopenai456"
# Simulate OAuth tokens (opaque, JWT-shaped, non-sk prefix)
OPAQUE_OAUTH = "oauth_tok_abcdef1234567890abcdef1234567890"
JWT_OAUTH = ".".join(
    (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJzdWIiOiJ1c2VyMTIzIn0",
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
    )
)


# ---------------------------------------------------------------------------
# detect_auth_type tests
# ---------------------------------------------------------------------------


class TestDetectAuthType:
    def test_x_api_key_is_apikey(self):
        headers = {"x-api-key": ANTHROPIC_API_KEY}
        assert detect_auth_type(headers) == AUTH_TYPE_APIKEY

    def test_bearer_sk_anthropic_is_apikey(self):
        headers = {"Authorization": f"Bearer {ANTHROPIC_API_KEY}"}
        assert detect_auth_type(headers) == AUTH_TYPE_APIKEY

    def test_bearer_sk_openai_is_apikey(self):
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        assert detect_auth_type(headers) == AUTH_TYPE_APIKEY

    def test_bearer_opaque_is_oauth(self):
        headers = {"Authorization": f"Bearer {OPAQUE_OAUTH}"}
        assert detect_auth_type(headers) == AUTH_TYPE_OAUTH

    def test_bearer_jwt_is_oauth(self):
        headers = {"Authorization": f"Bearer {JWT_OAUTH}"}
        assert detect_auth_type(headers) == AUTH_TYPE_OAUTH

    def test_no_auth_is_none(self):
        headers = {"Content-Type": "application/json"}
        assert detect_auth_type(headers) == AUTH_TYPE_NONE

    def test_case_insensitive_header_key(self):
        headers = {"AUTHORIZATION": f"Bearer {OPAQUE_OAUTH}"}
        assert detect_auth_type(headers) == AUTH_TYPE_OAUTH

    def test_empty_bearer_is_none(self):
        headers = {"Authorization": "Bearer "}
        assert detect_auth_type(headers) == AUTH_TYPE_NONE

    def test_x_api_key_takes_precedence(self):
        # If both x-api-key and Authorization are present, x-api-key wins
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "Authorization": f"Bearer {OPAQUE_OAUTH}",
        }
        assert detect_auth_type(headers) == AUTH_TYPE_APIKEY


# ---------------------------------------------------------------------------
# detect_token_format tests
# ---------------------------------------------------------------------------


class TestDetectTokenFormat:
    def test_apikey_format(self):
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        assert detect_token_format(headers) == "apikey"

    def test_jwt_format(self):
        headers = {"Authorization": f"Bearer {JWT_OAUTH}"}
        assert detect_token_format(headers) == "jwt"

    def test_opaque_format(self):
        headers = {"Authorization": f"Bearer {OPAQUE_OAUTH}"}
        assert detect_token_format(headers) == "opaque"

    def test_no_auth_is_unknown(self):
        headers = {}
        assert detect_token_format(headers) == "unknown"


# ---------------------------------------------------------------------------
# is_codex_model tests
# ---------------------------------------------------------------------------


class TestIsCodexModel:
    def test_gpt_52_codex(self):
        assert is_codex_model("gpt-5.2-codex") is True

    def test_gpt_51_codex_mini(self):
        assert is_codex_model("gpt-5.1-codex-mini") is True

    def test_gpt_53_codex_spark(self):
        assert is_codex_model("gpt-5.3-codex-spark") is True

    def test_regular_gpt4o_not_codex(self):
        assert is_codex_model("gpt-4o") is False

    def test_claude_not_codex(self):
        assert is_codex_model("claude-sonnet-4-6") is False

    def test_case_insensitive(self):
        assert is_codex_model("GPT-5.2-CODEX") is True


# ---------------------------------------------------------------------------
# analyze_request tests
# ---------------------------------------------------------------------------


class TestAnalyzeRequest:
    def test_anthropic_api_key_request(self):
        ctx = analyze_request(
            path="/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY},
            model="claude-sonnet-4-6",
        )
        assert ctx.auth_type == AUTH_TYPE_APIKEY
        assert ctx.is_codex is False
        assert ctx.skip_cache_keying is False

    def test_claude_code_oauth_request(self):
        """Claude Code subscription mode: Bearer OAuth token to /v1/messages"""
        ctx = analyze_request(
            path="/v1/messages",
            headers={"Authorization": f"Bearer {OPAQUE_OAUTH}"},
            model="claude-sonnet-4-6",
        )
        assert ctx.auth_type == AUTH_TYPE_OAUTH
        assert ctx.is_oauth_anthropic is True
        assert ctx.is_codex is False
        assert ctx.skip_cache_keying is True

    def test_codex_oauth_by_model(self):
        """Codex OAuth: Bearer OAuth token, model name contains 'codex'"""
        ctx = analyze_request(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
            model="gpt-5.2-codex",
        )
        assert ctx.auth_type == AUTH_TYPE_OAUTH
        assert ctx.is_codex is True
        assert ctx.skip_cache_keying is True

    def test_codex_oauth_by_path(self):
        """Codex via /v1/responses path"""
        ctx = analyze_request(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {OPAQUE_OAUTH}"},
            model="",
        )
        assert ctx.auth_type == AUTH_TYPE_OAUTH
        # path /v1/responses → codex unless it's anthropic path
        assert ctx.skip_cache_keying is True

    def test_openai_api_key_request(self):
        ctx = analyze_request(
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            model="gpt-4o",
        )
        assert ctx.auth_type == AUTH_TYPE_APIKEY
        assert ctx.skip_cache_keying is False

    def test_no_auth(self):
        ctx = analyze_request(
            path="/v1/messages",
            headers={},
        )
        assert ctx.auth_type == AUTH_TYPE_NONE
        assert ctx.skip_cache_keying is False


# ---------------------------------------------------------------------------
# oauth_telemetry_tags — security: no credential values
# ---------------------------------------------------------------------------


class TestOAuthTelemetryTags:
    def test_tags_contain_no_tokens(self):
        """Verify tags never contain the actual token value."""
        ctx = analyze_request(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
            model="gpt-5.2-codex",
        )
        tags = oauth_telemetry_tags(ctx)

        # Tags must not contain any fragment of the real token
        tag_str = json.dumps(tags)
        assert JWT_OAUTH not in tag_str
        assert JWT_OAUTH[:20] not in tag_str

    def test_codex_tags(self):
        ctx = analyze_request(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
            model="gpt-5.2-codex",
        )
        tags = oauth_telemetry_tags(ctx)
        assert tags["auth_type"] == AUTH_TYPE_OAUTH
        assert tags["provider_variant"] == "codex"
        assert tags["skip_cache"] == "true"

    def test_claude_code_oauth_tags(self):
        ctx = analyze_request(
            path="/v1/messages",
            headers={"Authorization": f"Bearer {OPAQUE_OAUTH}"},
            model="claude-sonnet-4-6",
        )
        tags = oauth_telemetry_tags(ctx)
        assert tags["provider_variant"] == "claude-code-oauth"


# ---------------------------------------------------------------------------
# ProviderRouter — Codex routing integration
# ---------------------------------------------------------------------------


class TestProviderRouterCodex:
    def setup_method(self):
        self.router = ProviderRouter()

    def test_routes_v1_responses_to_openai_codex(self):
        result = self.router.route(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
        )
        assert result.provider == "openai-codex"
        assert result.base_url == "https://chatgpt.com/backend-api"
        assert result.full_url == "https://chatgpt.com/backend-api/codex/responses"

    def test_routes_api_key_responses_to_openai_api(self):
        result = self.router.route(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        )
        assert result.provider == "openai"
        assert result.base_url == "https://api.openai.com"
        assert result.full_url == "https://api.openai.com/v1/responses"
        assert result.auth_type == AUTH_TYPE_APIKEY

    def test_routes_codex_model_body(self):
        body = json.dumps({"model": "gpt-5.2-codex", "messages": []}).encode()
        result = self.router.route(
            path="/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
            body=body,
        )
        assert result.provider == "openai-codex"
        assert result.is_codex is True
        assert result.auth_type == AUTH_TYPE_OAUTH
        assert result.skip_cache_keying is True

    def test_routes_anthropic_messages_unchanged(self):
        result = self.router.route(
            path="/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY},
        )
        assert result.provider == "anthropic"
        assert result.auth_type == AUTH_TYPE_APIKEY
        assert result.skip_cache_keying is False

    def test_claude_code_oauth_routes_to_anthropic(self):
        """OAuth Bearer to /v1/messages is Claude Code subscription → still anthropic provider"""
        result = self.router.route(
            path="/v1/messages",
            headers={"Authorization": f"Bearer {OPAQUE_OAUTH}"},
        )
        assert result.provider == "anthropic"
        assert result.auth_type == AUTH_TYPE_OAUTH
        assert result.skip_cache_keying is True

    def test_openai_api_key_routes_to_openai(self):
        body = json.dumps({"model": "gpt-4o", "messages": []}).encode()
        result = self.router.route(
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            body=body,
        )
        assert result.provider == "openai"
        assert result.auth_type == AUTH_TYPE_APIKEY

    def test_full_url_codex(self):
        result = self.router.route(
            path="https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {JWT_OAUTH}"},
        )
        assert result.provider == "openai"  # detected from host
        assert result.auth_type == AUTH_TYPE_OAUTH
        assert result.skip_cache_keying is True

    def test_route_result_has_auth_fields(self):
        """RouteResult always has auth_type, is_codex, skip_cache_keying fields."""
        result = self.router.route(
            path="/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY},
        )
        assert hasattr(result, "auth_type")
        assert hasattr(result, "is_codex")
        assert hasattr(result, "skip_cache_keying")
