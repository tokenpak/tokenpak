"""
Tests for tokenpak.proxy.credential_passthrough.CredentialPassthrough

Coverage:
  - validate_auth: valid headers pass
  - validate_auth: missing auth header rejected
  - validate_auth: malformed header rejected
  - build_forward_headers: each provider builds correct forwarding headers
  - build_forward_headers: unknown provider raises ValueError
  - No hardcoded API keys
  - Import works clean
"""

from __future__ import annotations

import pytest

from tokenpak.proxy.credential_passthrough import CredentialPassthrough

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cp() -> CredentialPassthrough:
    """Default CredentialPassthrough (require_auth=True)."""
    return CredentialPassthrough()


@pytest.fixture
def cp_noauth() -> CredentialPassthrough:
    """CredentialPassthrough with require_auth disabled."""
    return CredentialPassthrough(require_auth=False)


# ---------------------------------------------------------------------------
# validate_auth — valid cases
# ---------------------------------------------------------------------------

class TestValidateAuthValid:
    def test_bearer_token_passes(self, cp):
        ok, err = cp.validate_auth({"Authorization": "Bearer sk-testtoken"})
        assert ok is True
        assert err is None

    def test_x_api_key_passes(self, cp):
        ok, err = cp.validate_auth({"x-api-key": "sk-ant-testtoken"})
        assert ok is True
        assert err is None

    def test_authorization_case_insensitive(self, cp):
        ok, err = cp.validate_auth({"authorization": "Bearer sk-testtoken"})
        assert ok is True
        assert err is None

    def test_x_api_key_case_insensitive(self, cp):
        ok, err = cp.validate_auth({"X-API-KEY": "sk-testtoken"})
        assert ok is True
        assert err is None

    def test_require_auth_false_passes_without_headers(self, cp_noauth):
        ok, err = cp_noauth.validate_auth({})
        assert ok is True
        assert err is None

    def test_require_auth_false_passes_with_no_auth(self, cp_noauth):
        ok, err = cp_noauth.validate_auth({"Content-Type": "application/json"})
        assert ok is True
        assert err is None


# ---------------------------------------------------------------------------
# validate_auth — missing / malformed cases
# ---------------------------------------------------------------------------

class TestValidateAuthInvalid:
    def test_missing_auth_returns_false(self, cp):
        ok, err = cp.validate_auth({})
        assert ok is False
        assert err is not None

    def test_missing_auth_has_descriptive_message(self, cp):
        ok, err = cp.validate_auth({"Content-Type": "application/json"})
        assert ok is False
        assert err is not None
        assert len(err) > 10  # meaningful message

    def test_empty_authorization_value(self, cp):
        ok, err = cp.validate_auth({"Authorization": ""})
        assert ok is False
        assert err is not None

    def test_empty_x_api_key_value(self, cp):
        ok, err = cp.validate_auth({"x-api-key": "   "})
        assert ok is False
        assert err is not None

    def test_malformed_authorization_no_bearer_scheme(self, cp):
        ok, err = cp.validate_auth({"Authorization": "Token sk-testtoken"})
        assert ok is False
        assert err is not None

    def test_malformed_authorization_bare_key(self, cp):
        ok, err = cp.validate_auth({"Authorization": "sk-testtoken"})
        assert ok is False
        assert err is not None

    def test_malformed_authorization_bearer_only(self, cp):
        ok, err = cp.validate_auth({"Authorization": "Bearer"})
        assert ok is False
        assert err is not None

    def test_error_message_does_not_echo_credential(self, cp):
        """Security: error messages must never echo back the credential value."""
        fake = "MUST_NOT_APPEAR_IN_ERROR"
        ok, err = cp.validate_auth({"Authorization": f"Token {fake}"})
        assert ok is False
        assert fake not in (err or "")


# ---------------------------------------------------------------------------
# build_forward_headers — per-provider
# ---------------------------------------------------------------------------

class TestBuildForwardHeadersAnthropic:
    def test_auth_forwarded_as_x_api_key(self, cp):
        hdrs = cp.build_forward_headers({"Authorization": "Bearer sk-ant-abc123"}, provider="anthropic")
        assert "x-api-key" in hdrs
        assert "sk-ant-abc123" in hdrs["x-api-key"]

    def test_x_api_key_forwarded_unchanged(self, cp):
        hdrs = cp.build_forward_headers({"x-api-key": "sk-ant-abc123"}, provider="anthropic")
        assert hdrs.get("x-api-key") == "sk-ant-abc123"

    def test_anthropic_version_forwarded(self, cp):
        hdrs = cp.build_forward_headers({
            "x-api-key": "sk-ant-abc123",
            "anthropic-version": "2023-06-01",
        }, provider="anthropic")
        assert hdrs.get("anthropic-version") == "2023-06-01"

    def test_hop_by_hop_stripped(self, cp):
        hdrs = cp.build_forward_headers({
            "x-api-key": "sk-ant-abc123",
            "Connection": "keep-alive",
            "Host": "localhost",
            "Content-Length": "0",
        }, provider="anthropic")
        for h in ("Connection", "Host", "Content-Length"):
            assert h not in hdrs


class TestBuildForwardHeadersOpenAI:
    def test_auth_forwarded_as_authorization(self, cp):
        hdrs = cp.build_forward_headers({"Authorization": "Bearer sk-openai-abc"}, provider="openai")
        assert "Authorization" in hdrs
        assert "sk-openai-abc" in hdrs["Authorization"]

    def test_x_api_key_wrapped_as_bearer(self, cp):
        hdrs = cp.build_forward_headers({"x-api-key": "sk-openai-abc"}, provider="openai")
        assert "Authorization" in hdrs
        assert "Bearer" in hdrs["Authorization"]

    def test_hop_by_hop_stripped(self, cp):
        hdrs = cp.build_forward_headers({
            "Authorization": "Bearer sk-openai-abc",
            "Transfer-Encoding": "chunked",
            "Keep-Alive": "timeout=5",
        }, provider="openai")
        for h in ("Transfer-Encoding", "Keep-Alive"):
            assert h not in hdrs


class TestBuildForwardHeadersGoogle:
    def test_auth_forwarded_as_authorization(self, cp):
        hdrs = cp.build_forward_headers({"Authorization": "Bearer AIzaSy-abc123"}, provider="google")
        assert "Authorization" in hdrs
        assert "AIzaSy-abc123" in hdrs["Authorization"]

    def test_x_api_key_wrapped_as_bearer(self, cp):
        hdrs = cp.build_forward_headers({"x-api-key": "AIzaSy-abc123"}, provider="google")
        assert "Authorization" in hdrs
        assert "Bearer" in hdrs["Authorization"]

    def test_content_type_forwarded(self, cp):
        hdrs = cp.build_forward_headers({
            "Authorization": "Bearer AIzaSy-abc",
            "Content-Type": "application/json",
        }, provider="google")
        assert hdrs.get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# build_forward_headers — unknown provider raises ValueError
# ---------------------------------------------------------------------------

class TestBuildForwardHeadersUnknownProvider:
    def test_unknown_provider_raises(self, cp):
        with pytest.raises(ValueError, match="Unknown provider"):
            cp.build_forward_headers({"Authorization": "Bearer sk-test"}, provider="unknown")

    def test_cohere_raises(self, cp):
        with pytest.raises(ValueError):
            cp.build_forward_headers({"Authorization": "Bearer sk-test"}, provider="cohere")

    def test_empty_string_provider_raises(self, cp):
        with pytest.raises(ValueError):
            cp.build_forward_headers({"Authorization": "Bearer sk-test"}, provider="")


# ---------------------------------------------------------------------------
# mask_for_logging
# ---------------------------------------------------------------------------

class TestMaskForLogging:
    def test_authorization_redacted(self, cp):
        masked = cp.mask_for_logging({"Authorization": "Bearer sk-secret"})
        assert masked.get("Authorization") == "[REDACTED]"

    def test_x_api_key_redacted(self, cp):
        masked = cp.mask_for_logging({"x-api-key": "sk-ant-secret"})
        assert masked.get("x-api-key") == "[REDACTED]"

    def test_safe_headers_pass_through(self, cp):
        masked = cp.mask_for_logging({
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-secret",
        })
        assert masked["Content-Type"] == "application/json"
        assert masked["Authorization"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------

def test_import():
    """Acceptance criterion: import works clean with no side effects."""
    from tokenpak.proxy.credential_passthrough import CredentialPassthrough  # noqa: F401
    assert CredentialPassthrough is not None
