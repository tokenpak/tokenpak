"""
Tests for agent/proxy/passthrough.py — TokenPak Credential Passthrough

Covers:
- CredentialPassthrough.validate_auth: valid key, missing key → 401, malformed → 401
- CredentialPassthrough.build_forward_headers: auth forwarded unchanged, hop-by-hop stripped
- forward_headers() module-level shim
- validate_auth() module-level shim
- PassthroughConfig: require_auth=False skips validation
- Provider key format variations (Bearer sk-..., Bearer AIza..., x-api-key)
- mask_for_logging: credential values redacted
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from tokenpak.proxy.passthrough import (
    CredentialPassthrough,
    PassthroughConfig,
    forward_headers,
    validate_auth,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pt() -> CredentialPassthrough:
    """Default CredentialPassthrough (require_auth=True)."""
    return CredentialPassthrough()


@pytest.fixture
def pt_noauth() -> CredentialPassthrough:
    """CredentialPassthrough with require_auth disabled."""
    return CredentialPassthrough(PassthroughConfig(require_auth=False))


# ---------------------------------------------------------------------------
# validate_auth — valid cases
# ---------------------------------------------------------------------------

class TestValidateAuthValid:
    def test_bearer_openai_key(self, pt):
        ok, err = pt.validate_auth({"Authorization": "Bearer sk-abcdef1234"})
        assert ok is True
        assert err is None

    def test_bearer_anthropic_key(self, pt):
        ok, err = pt.validate_auth({"Authorization": "Bearer sk-ant-abcdef1234"})
        assert ok is True
        assert err is None

    def test_bearer_google_key(self, pt):
        ok, err = pt.validate_auth({"Authorization": "Bearer AIzaSyABCDEF1234"})
        assert ok is True
        assert err is None

    def test_x_api_key_raw(self, pt):
        ok, err = pt.validate_auth({"x-api-key": "sk-abcdef1234"})
        assert ok is True
        assert err is None

    def test_x_api_key_case_insensitive(self, pt):
        ok, err = pt.validate_auth({"X-API-Key": "sk-abcdef1234"})
        assert ok is True
        assert err is None

    def test_authorization_case_insensitive(self, pt):
        ok, err = pt.validate_auth({"authorization": "Bearer sk-abcdef1234"})
        assert ok is True
        assert err is None

    def test_require_auth_false_no_headers(self, pt_noauth):
        """When require_auth=False, missing auth is accepted."""
        ok, err = pt_noauth.validate_auth({})
        assert ok is True
        assert err is None


# ---------------------------------------------------------------------------
# validate_auth — 401 cases (missing / malformed)
# ---------------------------------------------------------------------------

class TestValidateAuthInvalid:
    def test_missing_auth_returns_false(self, pt):
        ok, err = pt.validate_auth({"Content-Type": "application/json"})
        assert ok is False
        assert err is not None
        assert "Missing" in err or "credential" in err.lower()

    def test_empty_headers_returns_false(self, pt):
        ok, err = pt.validate_auth({})
        assert ok is False
        assert err is not None

    def test_empty_authorization_value(self, pt):
        ok, err = pt.validate_auth({"Authorization": ""})
        assert ok is False
        assert err is not None

    def test_empty_x_api_key_value(self, pt):
        ok, err = pt.validate_auth({"x-api-key": "   "})
        assert ok is False
        assert err is not None

    def test_malformed_authorization_no_bearer(self, pt):
        ok, err = pt.validate_auth({"Authorization": "Token sk-abcdef1234"})
        assert ok is False
        assert err is not None
        assert "Bearer" in err or "Malformed" in err

    def test_malformed_authorization_bare_key(self, pt):
        ok, err = pt.validate_auth({"Authorization": "sk-abcdef1234"})
        assert ok is False
        assert err is not None

    def test_malformed_authorization_bearer_no_token(self, pt):
        ok, err = pt.validate_auth({"Authorization": "Bearer"})
        assert ok is False
        assert err is not None

    def test_module_level_validate_auth_missing(self):
        ok, err = validate_auth({})
        assert ok is False
        assert err is not None

    def test_module_level_validate_auth_valid(self):
        ok, err = validate_auth({"Authorization": "Bearer sk-test123"})
        assert ok is True
        assert err is None


# ---------------------------------------------------------------------------
# build_forward_headers — auth forwarded unchanged
# ---------------------------------------------------------------------------

class TestBuildForwardHeaders:
    def test_authorization_forwarded_unchanged(self, pt):
        raw_key = "Bearer sk-supersecret-key-12345"
        hdrs = pt.build_forward_headers({"Authorization": raw_key, "Content-Type": "application/json"})
        assert hdrs.get("Authorization") == raw_key

    def test_x_api_key_forwarded_unchanged(self, pt):
        raw_key = "sk-ant-api123"
        hdrs = pt.build_forward_headers({"x-api-key": raw_key})
        assert hdrs.get("x-api-key") == raw_key

    def test_hop_by_hop_headers_stripped(self, pt):
        incoming = {
            "Authorization": "Bearer sk-test",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "Keep-Alive": "timeout=5",
            "Host": "localhost:8766",
            "Content-Length": "42",
        }
        hdrs = pt.build_forward_headers(incoming)
        for hop in ("Connection", "Transfer-Encoding", "Keep-Alive", "Host", "Content-Length"):
            assert hop not in hdrs, f"Expected {hop} to be stripped"

    def test_content_type_forwarded(self, pt):
        hdrs = pt.build_forward_headers({
            "Authorization": "Bearer sk-test",
            "Content-Type": "application/json",
        })
        assert hdrs.get("Content-Type") == "application/json"

    def test_anthropic_version_forwarded(self, pt):
        hdrs = pt.build_forward_headers({
            "Authorization": "Bearer sk-ant-test",
            "anthropic-version": "2023-06-01",
        })
        assert hdrs.get("anthropic-version") == "2023-06-01"

    def test_empty_incoming_returns_empty(self, pt):
        hdrs = pt.build_forward_headers({})
        assert isinstance(hdrs, dict)

    def test_host_not_set_by_module(self, pt):
        """Host must be set by the caller, not passthrough."""
        hdrs = pt.build_forward_headers({"Authorization": "Bearer sk-test", "Host": "localhost"})
        # Host in strip list — should not be in result
        assert "Host" not in hdrs


# ---------------------------------------------------------------------------
# module-level forward_headers shim
# ---------------------------------------------------------------------------

class TestForwardHeadersShim:
    def test_returns_dict(self):
        result = forward_headers({"Authorization": "Bearer sk-test"})
        assert isinstance(result, dict)

    def test_auth_forwarded(self):
        key = "Bearer sk-test-shim"
        result = forward_headers({"Authorization": key})
        assert result.get("Authorization") == key

    def test_accepts_config_kwarg(self):
        cfg = PassthroughConfig(require_auth=False)
        result = forward_headers({"Content-Type": "application/json"}, config=cfg)
        assert isinstance(result, dict)

    def test_host_stripped(self):
        result = forward_headers({"Authorization": "Bearer sk-test", "Host": "example.com"})
        assert "Host" not in result


# ---------------------------------------------------------------------------
# mask_for_logging — credentials redacted
# ---------------------------------------------------------------------------

class TestMaskForLogging:
    def test_authorization_redacted(self, pt):
        masked = pt.mask_for_logging({"Authorization": "Bearer sk-supersecret"})
        assert masked.get("Authorization") == "[REDACTED]"

    def test_x_api_key_redacted(self, pt):
        masked = pt.mask_for_logging({"x-api-key": "sk-ant-supersecret"})
        assert masked.get("x-api-key") == "[REDACTED]"

    def test_safe_headers_pass_through(self, pt):
        masked = pt.mask_for_logging({
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "Authorization": "Bearer sk-secret",
        })
        assert masked["content-type"] == "application/json"
        assert masked["anthropic-version"] == "2023-06-01"
        assert masked["Authorization"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Zero-storage: verify no key values leak into exceptions or return values
# ---------------------------------------------------------------------------

class TestZeroStorageContract:
    def test_validate_auth_error_message_does_not_contain_key_value(self, pt):
        """Error messages must never echo back the credential value."""
        fake_key = "ULTRA_SECRET_KEY_THAT_MUST_NOT_APPEAR"
        ok, err = pt.validate_auth({"Authorization": f"Token {fake_key}"})
        assert ok is False
        assert fake_key not in (err or "")

    def test_forward_headers_does_not_alter_key_value(self, pt):
        raw = "Bearer sk-must-be-forwarded-exactly"
        hdrs = pt.build_forward_headers({"Authorization": raw})
        assert hdrs["Authorization"] == raw  # unchanged, not truncated/modified
