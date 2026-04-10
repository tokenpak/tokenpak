# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.security
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from tokenpak.security import (
    ensure_config_permissions,
    redact_pii,
    safe_temp_file,
    sanitize_cli_arg,
    sanitize_model_name,
    secure_write_config,
)


# ---------------------------------------------------------------------------
# secure_write_config
# ---------------------------------------------------------------------------


class TestSecureWriteConfig:
    def test_writes_valid_json(self, tmp_path):
        p = tmp_path / "config.json"
        data = {"key": "value", "num": 42}
        secure_write_config(p, data)
        assert p.exists()
        assert json.loads(p.read_text()) == data

    def test_file_permissions_are_600(self, tmp_path):
        p = tmp_path / "config.json"
        secure_write_config(p, {"x": 1})
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600

    def test_atomic_write_on_existing_file(self, tmp_path):
        p = tmp_path / "config.json"
        secure_write_config(p, {"v": 1})
        secure_write_config(p, {"v": 2})
        assert json.loads(p.read_text()) == {"v": 2}

    def test_raises_if_parent_missing(self, tmp_path):
        p = tmp_path / "nonexistent_dir" / "config.json"
        with pytest.raises(OSError):
            secure_write_config(p, {"x": 1})

    def test_nested_data_serialized(self, tmp_path):
        p = tmp_path / "config.json"
        data = {"a": [1, 2, 3], "b": {"c": True}}
        secure_write_config(p, data)
        assert json.loads(p.read_text()) == data


# ---------------------------------------------------------------------------
# ensure_config_permissions
# ---------------------------------------------------------------------------


class TestEnsureConfigPermissions:
    def test_returns_false_for_missing_file(self, tmp_path):
        assert ensure_config_permissions(tmp_path / "missing.json") is False

    def test_returns_true_when_already_600(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("{}")
        p.chmod(0o600)
        assert ensure_config_permissions(p) is True
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    def test_fixes_permissions_to_600(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("{}")
        p.chmod(0o644)
        result = ensure_config_permissions(p)
        assert result is True
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# sanitize_model_name
# ---------------------------------------------------------------------------


class TestSanitizeModelName:
    @pytest.mark.parametrize("name", [
        "gpt-4o",
        "claude-sonnet-4-6",
        "google/gemini-2-flash",
        "model_v1.2",
        "a",
    ])
    def test_valid_names_pass(self, name):
        assert sanitize_model_name(name) == name

    @pytest.mark.parametrize("name", [
        "../etc/passwd",
        "model;rm -rf /",
        "model|cat",
        "model$(evil)",
        "model`cmd`",
        "a" * 257,
        "",
    ])
    def test_invalid_names_raise(self, name):
        with pytest.raises(ValueError):
            sanitize_model_name(name)

    def test_raises_on_non_string(self):
        with pytest.raises(ValueError):
            sanitize_model_name(123)

    def test_blocks_path_traversal(self):
        with pytest.raises(ValueError):
            sanitize_model_name("some/../model")


# ---------------------------------------------------------------------------
# sanitize_cli_arg
# ---------------------------------------------------------------------------


class TestSanitizeCliArg:
    @pytest.mark.parametrize("value", [
        "hello",
        "valid-arg",
        "some_value123",
        "/absolute/path",
    ])
    def test_valid_args_pass(self, value):
        assert sanitize_cli_arg(value) == value

    @pytest.mark.parametrize("value", [
        "../etc/passwd",
        "value;rm -rf /",
        "val|cat /etc",
        "val&&evil",
        "$(whoami)",
        "`id`",
        "<script>alert(1)</script>",
        "javascript:void(0)",
    ])
    def test_injection_patterns_raise(self, value):
        with pytest.raises(ValueError):
            sanitize_cli_arg(value)

    def test_raises_on_non_string(self):
        with pytest.raises(ValueError):
            sanitize_cli_arg(42)

    def test_error_message_includes_name(self):
        with pytest.raises(ValueError, match="my_param"):
            sanitize_cli_arg("bad;input", name="my_param")


# ---------------------------------------------------------------------------
# redact_pii
# ---------------------------------------------------------------------------


class TestRedactPii:
    def test_redacts_sk_key(self):
        result = redact_pii("key=sk-abcdefghijk123")
        assert "sk-abcdefghijk123" not in result
        assert "[REDACTED-SK]" in result

    def test_redacts_bearer_token(self):
        result = redact_pii("Authorization: Bearer mytoken123")
        assert "mytoken123" not in result

    def test_redacts_api_key_json(self):
        result = redact_pii('{"api_key": "supersecret"}')
        assert "supersecret" not in result

    def test_safe_string_unchanged(self):
        text = "Hello world, no secrets here"
        assert redact_pii(text) == text

    def test_redacts_x_tokenpak_key_header(self):
        result = redact_pii("X-TokenPak-Key: secret-val-xyz")
        assert "secret-val-xyz" not in result
        assert "[REDACTED]" in result

    def test_multiple_patterns_in_one_string(self):
        text = "sk-abc12345678 and api_key=mykey"
        result = redact_pii(text)
        assert "sk-abc12345678" not in result
        assert "mykey" not in result

    def test_returns_string(self):
        assert isinstance(redact_pii("test"), str)


# ---------------------------------------------------------------------------
# safe_temp_file
# ---------------------------------------------------------------------------


class TestSafeTempFile:
    def test_returns_fd_and_path(self, tmp_path):
        fd, path = safe_temp_file(dir=tmp_path)
        assert isinstance(fd, int)
        assert isinstance(path, str)
        os.close(fd)
        os.unlink(path)

    def test_file_permissions_are_600(self, tmp_path):
        fd, path = safe_temp_file(dir=tmp_path)
        os.close(fd)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
        os.unlink(path)

    def test_custom_suffix(self, tmp_path):
        fd, path = safe_temp_file(suffix=".json", dir=tmp_path)
        os.close(fd)
        assert path.endswith(".json")
        os.unlink(path)
