# SPDX-License-Identifier: Apache-2.0
"""Tests for the /codex/responses path matcher + caller-creds precedence."""

from __future__ import annotations

from tokenpak.proxy.router import ProviderRouter
from tokenpak.proxy.server import _ProxyHandler


class TestCodexPathRouting:
    """``/codex/responses`` (OpenClaw pi-ai shape) routes to openai-codex."""

    def test_codex_responses_detected_as_openai_codex(self):
        router = ProviderRouter()
        result = router.route("/codex/responses", {})
        assert result.provider == "openai-codex"

    def test_v1_responses_still_detected_as_openai_codex(self):
        router = ProviderRouter()
        result = router.route("/v1/responses", {})
        assert result.provider == "openai-codex"

    def test_codex_responses_path_in_full_url(self):
        router = ProviderRouter()
        result = router.route("/codex/responses", {})
        # The base URL for openai-codex is api.openai.com; the
        # CodexCredentialProvider overrides this to chatgpt.com on
        # injection. Router itself just builds base + path.
        assert "/codex/responses" in result.full_url


class TestCallerHasCredentials:
    """``_caller_has_credentials`` distinguishes real auth from stale placeholders."""

    def test_sk_api_key_via_x_api_key(self):
        assert _ProxyHandler._caller_has_credentials({"x-api-key": "sk-abc123def456"}) is True

    def test_sk_api_key_via_bearer(self):
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": "Bearer sk-abc123def456"}
            )
            is True
        )

    def test_long_jwt_via_bearer(self):
        long_jwt = "eyJ" + "a" * 200 + ".x.y"
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": f"Bearer {long_jwt}"}
            )
            is True
        )

    def test_short_jwt_rejected(self):
        # OpenClaw sometimes ships short stale OAuth-shape Bearer tokens
        # (≤100 chars). These shouldn't count as "caller has creds".
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": "Bearer eyJabc.short.token"}
            )
            is False
        )

    def test_jwt_without_dot_rejected(self):
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": "Bearer eyJ" + "a" * 300}
            )
            is False
        )

    def test_placeholder_bearer_rejected(self):
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": "Bearer placeholder-not-real"}
            )
            is False
        )

    def test_no_auth_headers(self):
        assert _ProxyHandler._caller_has_credentials({}) is False

    def test_empty_headers_dict(self):
        assert _ProxyHandler._caller_has_credentials({}) is False

    def test_case_insensitive_header_lookup(self):
        assert (
            _ProxyHandler._caller_has_credentials({"X-Api-Key": "sk-abc123def456"})
            is True
        )
        assert (
            _ProxyHandler._caller_has_credentials(
                {"AUTHORIZATION": "Bearer sk-abc123def456"}
            )
            is True
        )

    def test_non_sk_non_jwt_bearer_rejected(self):
        # Random opaque tokens (e.g. session ids) shouldn't trigger
        # passthrough — we wouldn't know what to do with them upstream.
        assert (
            _ProxyHandler._caller_has_credentials(
                {"Authorization": "Bearer opaque-session-token-12345"}
            )
            is False
        )
