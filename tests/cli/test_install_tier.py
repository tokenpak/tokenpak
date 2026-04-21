"""TPS-02 acceptance: install-tier subcommand behavior."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.cli._install_tier import (
    VALID_TIERS,
    _index_url_with_auth,
    _license_key,
    _license_tier,
    run_install_tier,
)


def test_invalid_tier_returns_2(capsys):
    rc = run_install_tier("gold", dry_run=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "Unknown tier" in captured.err


def test_no_license_file_returns_2(tmp_path, monkeypatch, capsys):
    fake_file = tmp_path / "nope.json"
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(fake_file))
    # Need to re-import to pick up env change
    from importlib import reload
    import tokenpak.cli._install_tier as mod
    reload(mod)
    rc = mod.run_install_tier("pro", dry_run=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "No license activated" in captured.err


def test_license_missing_key_returns_2(tmp_path, monkeypatch, capsys):
    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps({"tier": "pro"}))  # no key field
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(license_file))
    from importlib import reload
    import tokenpak.cli._install_tier as mod
    reload(mod)
    rc = mod.run_install_tier("pro", dry_run=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing a usable license key" in captured.err


def test_dry_run_with_valid_license_returns_0(tmp_path, monkeypatch, capsys):
    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps({"key": "TPK-PRO-TESTKEY123", "tier": "pro"}))
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(license_file))
    from importlib import reload
    import tokenpak.cli._install_tier as mod
    reload(mod)
    rc = mod.run_install_tier("pro", dry_run=True)
    assert rc == 0
    captured = capsys.readouterr()
    # Key must be redacted in display
    assert "<REDACTED>" in captured.out
    assert "TPK-PRO-TESTKEY123" not in captured.out


def test_license_key_extraction_supports_multiple_field_names():
    assert _license_key({"key": "abc"}) == "abc"
    assert _license_key({"license_key": "def"}) == "def"
    assert _license_key({"token": "ghi"}) == "ghi"
    assert _license_key({"pkg_access_token": "jkl"}) == "jkl"
    assert _license_key({}) is None
    assert _license_key({"key": ""}) is None
    assert _license_key({"key": "   "}) is None


def test_license_tier_extraction_normalizes_case():
    assert _license_tier({"tier": "Pro"}) == "pro"
    assert _license_tier({"tier": "TEAM"}) == "team"
    assert _license_tier({"plan": "enterprise"}) == "enterprise"
    assert _license_tier({}) is None


def test_index_url_injects_auth():
    with patch("tokenpak.cli._install_tier.PRIVATE_INDEX_URL", "https://pypi.tokenpak.ai/simple/"):
        auth_url = _index_url_with_auth("SECRET-KEY")
    assert auth_url == "https://__token__:SECRET-KEY@pypi.tokenpak.ai/simple/"


def test_tier_mismatch_warns_but_proceeds(tmp_path, monkeypatch, capsys):
    """A user with a Pro license requesting install-tier team proceeds with a note."""
    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps({"key": "TPK-PRO-X", "tier": "pro"}))
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(license_file))
    from importlib import reload
    import tokenpak.cli._install_tier as mod
    reload(mod)
    rc = mod.run_install_tier("team", dry_run=True)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Your license tier is 'pro'" in captured.out
    assert "you requested install-tier 'team'" in captured.out
