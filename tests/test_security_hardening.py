"""
Tests for TokenPak Security Hardening (task 2.10)

Coverage
────────
File Permissions
 1.  secure_write_config creates file with mode 600
 2.  secure_write_config content is valid JSON
 3.  secure_write_config is atomic (no partial writes)
 4.  ensure_config_permissions fixes wrong permissions
 5.  ensure_config_permissions returns False when file missing
 6.  safe_temp_file creates with mode 600

Input Validation — sanitize_model_name
 7.  Valid simple model name passes
 8.  Valid namespaced model name passes (google/gemini-2-flash)
 9.  Model name with shell injection rejected
 10. Model name with path traversal rejected
 11. Model name with semicolon rejected
 12. Model name with backtick rejected
 13. Empty model name rejected

Input Validation — sanitize_cli_arg
 14. Clean CLI arg passes
 15. Path traversal in CLI arg rejected
 16. Shell pipe in CLI arg rejected
 17. Command substitution in CLI arg rejected
 18. Script injection rejected

PII / Credential Redaction
 19. redact_pii removes sk- style keys
 20. redact_pii removes Bearer tokens
 21. redact_pii removes X-TokenPak-Key values
 22. redact_pii removes Authorization: Bearer values
 23. redact_pii removes api_key JSON fields
 24. redact_pii passes clean text unchanged

Server — model name injection rejected
 25. POST /v1/compress with injected model name returns 422
 26. POST /v1/budget with injected model name returns 422
 27. POST /v1/compress with path-traversal model returns 422

Content not logged
 28. compress body.content never appears in log output
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.intelligence.server", reason="module not available in current build")
import json
import logging
import os
import stat
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tokenpak.intelligence.auth import APIKeyValidator, LicenseTier, RateLimiter
from tokenpak.intelligence.server import create_app
from tokenpak.security import (
    ensure_config_permissions,
    redact_pii,
    safe_temp_file,
    sanitize_cli_arg,
    sanitize_model_name,
    secure_write_config,
)

# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture()
def validator():
    v = APIKeyValidator()
    v.register("key-pro", LicenseTier.PRO)
    v.register("key-enterprise", LicenseTier.ENTERPRISE)
    return v


@pytest.fixture()
def client(validator):
    app = create_app(validator=validator, limiter=RateLimiter())
    return TestClient(app, raise_server_exceptions=False)


# ──────────────────────────────────────────────────────────────
# 1-6  File Permissions
# ──────────────────────────────────────────────────────────────


def test_secure_write_config_mode_600(tmp_dir):
    """Config file must be created with mode 600."""
    cfg = tmp_dir / "config.json"
    secure_write_config(cfg, {"port": 8766})
    mode = oct(stat.S_IMODE(cfg.stat().st_mode))
    assert mode == oct(0o600), f"Expected 0o600, got {mode}"


def test_secure_write_config_valid_json(tmp_dir):
    """Written config must be valid, parseable JSON."""
    cfg = tmp_dir / "config.json"
    data = {"version": "1.0", "port": 9000, "compress": True, "nested": {"a": 1}}
    secure_write_config(cfg, data)
    loaded = json.loads(cfg.read_text())
    assert loaded == data


def test_secure_write_config_atomic_no_partial(tmp_dir):
    """An error mid-write must not leave a corrupt file."""
    cfg = tmp_dir / "config.json"
    # Pre-write valid content
    secure_write_config(cfg, {"original": True})

    # Simulate failure by raising inside json.dump
    with patch("tokenpak.security.json.dump", side_effect=IOError("disk full")):
        with pytest.raises(IOError):
            secure_write_config(cfg, {"broken": True})

    # Original file should still be intact
    data = json.loads(cfg.read_text())
    assert data == {"original": True}


def test_ensure_config_permissions_fixes_wrong_mode(tmp_dir):
    """ensure_config_permissions must chmod to 600 if mode is wrong."""
    cfg = tmp_dir / "config.json"
    cfg.write_text('{"x": 1}')
    cfg.chmod(0o644)  # world-readable
    ensure_config_permissions(cfg)
    assert oct(stat.S_IMODE(cfg.stat().st_mode)) == oct(0o600)


def test_ensure_config_permissions_missing_returns_false(tmp_dir):
    """Returns False when the target file does not exist."""
    result = ensure_config_permissions(tmp_dir / "nonexistent.json")
    assert result is False


def test_safe_temp_file_mode_600(tmp_dir):
    """safe_temp_file must create a temp file with mode 600."""
    fd, path = safe_temp_file(dir=tmp_dir)
    try:
        mode = oct(stat.S_IMODE(os.stat(path).st_mode))
        assert mode == oct(0o600), f"Expected 0o600, got {mode}"
    finally:
        os.close(fd)
        os.unlink(path)


# ──────────────────────────────────────────────────────────────
# 7-13  sanitize_model_name
# ──────────────────────────────────────────────────────────────


def test_sanitize_model_name_simple():
    assert sanitize_model_name("gpt-4o") == "gpt-4o"


def test_sanitize_model_name_namespaced():
    assert sanitize_model_name("google/gemini-2-flash") == "google/gemini-2-flash"


def test_sanitize_model_name_with_version():
    assert sanitize_model_name("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_sanitize_model_name_shell_injection_rejected():
    with pytest.raises(ValueError, match="Invalid model name"):
        sanitize_model_name("gpt-4o; rm -rf /")


def test_sanitize_model_name_path_traversal_rejected():
    with pytest.raises(ValueError, match="Invalid model name"):
        sanitize_model_name("../../etc/passwd")


def test_sanitize_model_name_semicolon_rejected():
    with pytest.raises(ValueError, match="Invalid model name"):
        sanitize_model_name("gpt-4o;curl evil.com")


def test_sanitize_model_name_backtick_rejected():
    with pytest.raises(ValueError, match="Invalid model name"):
        sanitize_model_name("gpt-4o`id`")


def test_sanitize_model_name_empty_rejected():
    with pytest.raises(ValueError):
        sanitize_model_name("")


# ──────────────────────────────────────────────────────────────
# 14-18  sanitize_cli_arg
# ──────────────────────────────────────────────────────────────


def test_sanitize_cli_arg_clean():
    assert sanitize_cli_arg("my-project-dir") == "my-project-dir"


def test_sanitize_cli_arg_path_traversal_rejected():
    with pytest.raises(ValueError, match="disallowed"):
        sanitize_cli_arg("../../secret", name="path")


def test_sanitize_cli_arg_pipe_rejected():
    with pytest.raises(ValueError, match="disallowed"):
        sanitize_cli_arg("value|cat /etc/passwd", name="name")


def test_sanitize_cli_arg_command_substitution_rejected():
    with pytest.raises(ValueError, match="disallowed"):
        sanitize_cli_arg("$(id)", name="cmd")


def test_sanitize_cli_arg_script_injection_rejected():
    with pytest.raises(ValueError, match="disallowed"):
        sanitize_cli_arg("<script>alert(1)</script>", name="content")


# ──────────────────────────────────────────────────────────────
# 19-24  redact_pii
# ──────────────────────────────────────────────────────────────


def test_redact_pii_sk_key():
    result = redact_pii("Using key sk-abcdefghijk1234567890")
    assert "sk-abcdefghijk1234567890" not in result
    assert "[REDACTED" in result


def test_redact_pii_bearer_token():
    result = redact_pii("Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJSUzI1NiJ9" not in result
    assert "[REDACTED]" in result


def test_redact_pii_tokenpak_key_header():
    result = redact_pii("X-TokenPak-Key: tp-secret-key-value")
    assert "tp-secret-key-value" not in result
    assert "[REDACTED]" in result


def test_redact_pii_authorization_bearer():
    result = redact_pii("Authorization: Bearer my-super-secret-token")
    assert "my-super-secret-token" not in result
    assert "[REDACTED]" in result


def test_redact_pii_api_key_json():
    result = redact_pii('{"api_key": "hidden-value-123"}')
    assert "hidden-value-123" not in result
    assert "[REDACTED]" in result


def test_redact_pii_clean_text_unchanged():
    clean = "The quick brown fox jumps over the lazy dog."
    assert redact_pii(clean) == clean


# ──────────────────────────────────────────────────────────────
# 25-27  Server — model name injection → 422
# ──────────────────────────────────────────────────────────────


def test_compress_injected_model_returns_422(client):
    """Model name with shell injection must be rejected with 422."""
    resp = client.post(
        "/v1/compress",
        headers={"X-TokenPak-Key": "key-pro"},
        json={"content": "Hello world", "model": "gpt-4o; rm -rf /"},
    )
    assert resp.status_code == 422


def test_budget_injected_model_returns_422(client):
    """Budget endpoint must also reject injected model name."""
    resp = client.post(
        "/v1/budget",
        headers={"X-TokenPak-Key": "key-pro"},
        json={"content": "Hello world", "model": "../../etc/passwd"},
    )
    assert resp.status_code == 422


def test_compress_path_traversal_model_returns_422(client):
    """Path traversal in model name must be rejected."""
    resp = client.post(
        "/v1/compress",
        headers={"X-TokenPak-Key": "key-pro"},
        json={"content": "Hello world", "model": "../../../root/.ssh/id_rsa"},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# 28  Content never logged
# ──────────────────────────────────────────────────────────────


def test_compress_content_not_in_logs(validator, caplog):
    """Request body content must never appear in log output."""
    app = create_app(validator=validator, limiter=RateLimiter())
    client = TestClient(app, raise_server_exceptions=False)

    secret_payload = "SUPER_SECRET_CONTENT_XYZ_12345"
    with caplog.at_level(logging.DEBUG, logger="tokenpak"):
        resp = client.post(
            "/v1/compress",
            headers={"X-TokenPak-Key": "key-pro"},
            json={"content": secret_payload, "model": "gpt-4o"},
        )
    assert resp.status_code == 200
    assert secret_payload not in caplog.text, (
        "Request body content must not appear in logs"
    )
