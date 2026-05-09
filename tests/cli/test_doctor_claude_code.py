"""tests/cli/test_doctor_claude_code.py

Tests for the CCI-12 + CCP-09 Claude Code health checks:
  tokenpak doctor --claude-code

Coverage:
  0. Healthy install — all 9 checks pass
  1. Check 1 fails when ANTHROPIC_BASE_URL is not set
  2. Check 2 fails when proxy is unreachable
  3. Check 3 fails when count_tokens returns non-200
  4. Check 4 fails when active profile is not claude-code-*
  5. Check 5 fails when round-trip returns non-200
  6. Check 6 passes (skip) when no traffic and no DB
  7. Check 7 fails on PYTHONPATH drift (2026-04-08 incident pattern)
  8. Check 8 fails when sources disagree on proxy URL
  9. Check 9 fails when neither plugin dir candidate exists
  Additional: Check 7 passes when PYTHONPATH matches canonical
  Additional: Check 8 passes when all sources agree
  Additional: Check 9 passes when tokenpak dir exists
  Additional: Check 9 passes when tokenpak-claude-code dir exists
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.cli.commands.doctor_claude_code import (
    NUM_CHECKS,
    _check_active_profile,
    _check_auth_flow,
    _check_base_url_set,
    _check_install_consistency,
    _check_plugin_dir,
    _check_proxy_reachable,
    _check_pythonpath_drift,
    _check_sample_roundtrip,
    _check_telemetry_visible,
    _normalise_url,
    run_claude_code_checks,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Redirect Path.home() to tmp_path so all config files are isolated."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


@pytest.fixture()
def healthy_env(tmp_home, monkeypatch):
    """Set up a minimal environment that satisfies all 9 checks (mocked network)."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    monkeypatch.setenv("TOKENPAK_PROFILE", "claude-code-cli")
    yield tmp_home


# ---------------------------------------------------------------------------
# Test 0: Healthy install — all 9 checks pass
# ---------------------------------------------------------------------------


def test_healthy_install_all_9_pass(tmp_home, monkeypatch):
    """All 9 checks pass when network is mocked healthy and env is correct."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    monkeypatch.setenv("TOKENPAK_PROFILE", "claude-code-cli")

    # Proxy PID file
    pid = 99999
    (tmp_home / ".tokenpak").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".tokenpak" / "proxy.pid").write_text(str(pid))

    # Canonical PYTHONPATH in systemd unit
    unit_dir = tmp_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    canonical_pp = f"{tmp_home}/.local/lib/python3.12/site-packages:{tmp_home}/tokenpak"
    (unit_dir / "tokenpak-proxy.service").write_text(
        f"[Service]\nEnvironment=PYTHONPATH={canonical_pp}\n"
        f"Environment=TOKENPAK_PORT=8766\n"
    )

    # tokenpak.env
    (tmp_home / ".config").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".config" / "tokenpak.env").write_text("TOKENPAK_PORT=8766\n")

    # ~/.claude/settings.json + plugin dir
    (tmp_home / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".claude" / "settings.json").write_text(
        json.dumps({"anthropicBaseUrl": "http://127.0.0.1:8766"})
    )
    (tmp_home / ".claude" / "plugins" / "tokenpak").mkdir(parents=True, exist_ok=True)

    def mock_http_get(url, timeout=4):
        if "/health" in url:
            return 200, json.dumps({"compilation_mode": "hybrid"}).encode()
        if "/stats" in url:
            return 200, json.dumps({"session": {"active_profile": "claude-code-cli"}}).encode()
        if "/v1/sessions" in url:
            return 200, json.dumps({"sessions": [], "total": 5}).encode()
        return 200, b"{}"

    def mock_http_post(url, payload, headers=None, timeout=8):
        if "count_tokens" in url:
            return 200, json.dumps({"input_tokens": 3}).encode()
        return 200, b"{}"

    def mock_proc_environ(p):
        return {"PYTHONPATH": canonical_pp, "HOME": str(tmp_home)}

    with (
        mock.patch("tokenpak.cli.commands.doctor_claude_code._http_get", side_effect=mock_http_get),
        mock.patch("tokenpak.cli.commands.doctor_claude_code._http_post_json", side_effect=mock_http_post),
        mock.patch("tokenpak.cli.commands.doctor_claude_code._read_proc_environ", side_effect=mock_proc_environ),
        mock.patch("tokenpak.cli.commands.doctor_claude_code._get_proxy_pid", return_value=pid),
    ):
        fail_count, results = run_claude_code_checks()

    assert len(results) == NUM_CHECKS, f"Expected {NUM_CHECKS} results, got {len(results)}"
    failed = [r for r in results if r["status"] == "fail"]
    assert fail_count == 0, f"Expected 0 failures, got {fail_count}: {[r['check'] for r in failed]}"
    assert all(r["status"] == "pass" for r in results), (
        f"Not all checks passed: {[(r['check'], r['status']) for r in results if r['status'] != 'pass']}"
    )


# ---------------------------------------------------------------------------
# Test 1: ANTHROPIC_BASE_URL not set → check 1 fails
# ---------------------------------------------------------------------------


def test_check1_base_url_not_set(tmp_home, monkeypatch):
    """Check 1 fails when ANTHROPIC_BASE_URL is not set and settings.json is absent."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    (tmp_home / ".claude").mkdir(parents=True, exist_ok=True)
    # settings.json without anthropicBaseUrl
    (tmp_home / ".claude" / "settings.json").write_text(json.dumps({"skipPrompt": True}))

    result = _check_base_url_set()
    assert result["status"] == "fail"
    assert "NOT SET" in result["message"]
    assert result["remediation"]


def test_check1_base_url_from_env(monkeypatch):
    """Check 1 passes when ANTHROPIC_BASE_URL is set in env."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    result = _check_base_url_set()
    assert result["status"] == "pass"
    assert "127.0.0.1:8766" in result["message"]


def test_check1_base_url_from_settings(tmp_home, monkeypatch):
    """Check 1 passes when anthropicBaseUrl is in settings.json (env not set)."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    (tmp_home / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".claude" / "settings.json").write_text(
        json.dumps({"anthropicBaseUrl": "http://127.0.0.1:9000"})
    )
    result = _check_base_url_set()
    assert result["status"] == "pass"
    assert "9000" in result["message"]


# ---------------------------------------------------------------------------
# Test 2: Proxy unreachable → check 2 fails
# ---------------------------------------------------------------------------


def test_check2_proxy_unreachable(monkeypatch):
    """Check 2 fails when proxy returns status 0 (connection refused)."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    with mock.patch("tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(0, b"")):
        result = _check_proxy_reachable()

    assert result["status"] == "fail"
    assert "UNREACHABLE" in result["message"]
    assert result["remediation"]


def test_check2_proxy_reachable(monkeypatch):
    """Check 2 passes when proxy returns 200 from /health."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    health_body = json.dumps({"compilation_mode": "hybrid"}).encode()

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(200, health_body)
    ):
        result = _check_proxy_reachable()

    assert result["status"] == "pass"
    assert "hybrid" in result["message"]


# ---------------------------------------------------------------------------
# Test 3: Auth flow fails → check 3 fails
# ---------------------------------------------------------------------------


def test_check3_auth_flow_fails(monkeypatch):
    """Check 3 fails when count_tokens returns non-200."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_post_json",
        return_value=(401, b'{"error":"unauthorized"}'),
    ):
        result = _check_auth_flow()

    assert result["status"] == "fail"
    assert "401" in result["message"]
    assert result["remediation"]


def test_check3_auth_flow_passes(monkeypatch):
    """Check 3 passes when count_tokens returns 200."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    body = json.dumps({"input_tokens": 2}).encode()

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_post_json",
        return_value=(200, body),
    ):
        result = _check_auth_flow()

    assert result["status"] == "pass"
    assert "OK" in result["message"]


# ---------------------------------------------------------------------------
# Test 4: Active profile not claude-code-* → check 4 fails
# ---------------------------------------------------------------------------


def test_check4_profile_not_claude_code(monkeypatch):
    """Check 4 fails when TOKENPAK_PROFILE is not claude-code-*."""
    monkeypatch.setenv("TOKENPAK_PROFILE", "balanced")
    monkeypatch.delenv("TOKENPAK_PROFILE_OVERRIDE", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    # Stats returns a non-claude-code profile
    stats_body = json.dumps({"session": {"active_profile": "balanced"}}).encode()
    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(200, stats_body)
    ):
        result = _check_active_profile()

    assert result["status"] == "fail"
    assert "claude-code" in result["message"]
    assert result["remediation"]


def test_check4_profile_via_env(monkeypatch):
    """Check 4 passes when TOKENPAK_PROFILE starts with claude-code-."""
    monkeypatch.setenv("TOKENPAK_PROFILE", "claude-code-cli")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(0, b"")
    ):
        result = _check_active_profile()

    assert result["status"] == "pass"
    assert "claude-code" in result["message"]


# ---------------------------------------------------------------------------
# Test 5: Round-trip fails → check 5 fails
# ---------------------------------------------------------------------------


def test_check5_roundtrip_fails(monkeypatch):
    """Check 5 fails when proxy returns non-200 for the round-trip probe."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_post_json",
        return_value=(503, b'{"error":"overloaded"}'),
    ):
        result = _check_sample_roundtrip()

    assert result["status"] == "fail"
    assert "503" in result["message"]
    assert result["remediation"]


def test_check5_roundtrip_passes(monkeypatch):
    """Check 5 passes when the probe round-trip succeeds."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    body = json.dumps({"input_tokens": 5}).encode()

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_post_json",
        return_value=(200, body),
    ):
        result = _check_sample_roundtrip()

    assert result["status"] == "pass"
    assert "OK" in result["message"]


# ---------------------------------------------------------------------------
# Test 6: No traffic / no DB → check 6 skips (pass)
# ---------------------------------------------------------------------------


def test_check6_no_traffic_skip(tmp_home, monkeypatch):
    """Check 6 passes (skip) when no monitor.db and /v1/sessions unavailable."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    # No monitor.db in tmp_home/.tokenpak

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(404, b"not found")
    ):
        result = _check_telemetry_visible()

    assert result["status"] == "pass"
    assert "SKIP" in result["message"] or "no traffic" in result["message"].lower()


def test_check6_sessions_via_endpoint(monkeypatch):
    """Check 6 passes when /v1/sessions returns sessions."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    body = json.dumps({"sessions": [{"id": "abc"}], "total": 3}).encode()

    with mock.patch(
        "tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(200, body)
    ):
        result = _check_telemetry_visible()

    assert result["status"] == "pass"
    assert "3" in result["message"]


# ---------------------------------------------------------------------------
# Test 7: PYTHONPATH drift (2026-04-08 incident pattern) → check 7 fails
# ---------------------------------------------------------------------------


def test_check7_pythonpath_drift_incident(tmp_home, monkeypatch):
    """Check 7 detects drift where proc PYTHONPATH references the wrong user's home (2026-04-08 incident pattern)."""
    pid = 42001
    (tmp_home / ".tokenpak").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".tokenpak" / "proxy.pid").write_text(str(pid))

    canonical_pp = f"{tmp_home}/.local/lib/python3.12/site-packages:{tmp_home}/tokenpak"
    unit_dir = tmp_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "tokenpak-proxy.service").write_text(
        f"[Service]\nEnvironment=PYTHONPATH={canonical_pp}\n"
    )

    # Proc environ has /home/sue/ instead of the canonical path
    drifted_pp = "/home/sue/.local/lib/python3.12/site-packages:/home/sue/vault/01_PROJECTS/tokenpak/tokenpak"

    with (
        mock.patch("tokenpak.cli.commands.doctor_claude_code._get_proxy_pid", return_value=pid),
        mock.patch(
            "tokenpak.cli.commands.doctor_claude_code._read_proc_environ",
            return_value={"PYTHONPATH": drifted_pp},
        ),
    ):
        result = _check_pythonpath_drift()

    assert result["status"] == "fail"
    assert "DRIFT" in result["message"]
    assert "sue" in result["detail"] or "sue" in result["message"]
    assert result["remediation"]


def test_check7_pythonpath_no_drift(tmp_home, monkeypatch):
    """Check 7 passes when proc PYTHONPATH matches the canonical value from the unit file."""
    pid = 42002
    (tmp_home / ".tokenpak").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".tokenpak" / "proxy.pid").write_text(str(pid))

    canonical_pp = f"{tmp_home}/.local/lib/python3.12/site-packages:{tmp_home}/tokenpak"
    unit_dir = tmp_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "tokenpak-proxy.service").write_text(
        f"[Service]\nEnvironment=PYTHONPATH={canonical_pp}\n"
    )

    with (
        mock.patch("tokenpak.cli.commands.doctor_claude_code._get_proxy_pid", return_value=pid),
        mock.patch(
            "tokenpak.cli.commands.doctor_claude_code._read_proc_environ",
            return_value={"PYTHONPATH": canonical_pp},
        ),
    ):
        result = _check_pythonpath_drift()

    assert result["status"] == "pass"
    assert "OK" in result["message"]


def test_check7_no_pid_file(tmp_home, monkeypatch):
    """Check 7 fails gracefully when proxy.pid is missing."""
    # No pid file — use tmp_home which has no .tokenpak/proxy.pid
    with mock.patch("tokenpak.cli.commands.doctor_claude_code._get_proxy_pid", return_value=None):
        result = _check_pythonpath_drift()

    assert result["status"] == "fail"
    assert "CANNOT CHECK" in result["message"]


# ---------------------------------------------------------------------------
# Test 8: Install consistency mismatch → check 8 fails
# ---------------------------------------------------------------------------


def test_check8_inconsistency_detected(tmp_home, monkeypatch):
    """Check 8 fails when sources report different proxy URLs."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    (tmp_home / ".config").mkdir(parents=True, exist_ok=True)
    # tokenpak.env points to port 9000 (different!)
    (tmp_home / ".config" / "tokenpak.env").write_text("TOKENPAK_PORT=9000\n")

    unit_dir = tmp_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    # systemd unit points to port 8766
    (unit_dir / "tokenpak-proxy.service").write_text(
        "[Service]\nEnvironment=TOKENPAK_PORT=8766\n"
    )

    result = _check_install_consistency()
    assert result["status"] == "fail"
    assert "MISMATCH" in result["message"]
    assert result["remediation"]


def test_check8_consistency_ok(tmp_home, monkeypatch):
    """Check 8 passes when all sources agree on the same proxy URL."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")

    (tmp_home / ".config").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".config" / "tokenpak.env").write_text("TOKENPAK_PORT=8766\n")

    unit_dir = tmp_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "tokenpak-proxy.service").write_text(
        "[Service]\nEnvironment=TOKENPAK_PORT=8766\n"
    )

    (tmp_home / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".claude" / "settings.json").write_text(
        json.dumps({"anthropicBaseUrl": "http://127.0.0.1:8766"})
    )

    result = _check_install_consistency()
    assert result["status"] == "pass"
    assert "OK" in result["message"] or "consistent" in result["message"].lower()


# ---------------------------------------------------------------------------
# Test: run_claude_code_checks returns correct count and NUM_CHECKS results
# ---------------------------------------------------------------------------


def test_run_returns_9_results(monkeypatch):
    """run_claude_code_checks always returns exactly NUM_CHECKS (9) results."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8766")
    monkeypatch.setenv("TOKENPAK_PROFILE", "claude-code-cli")

    with (
        mock.patch("tokenpak.cli.commands.doctor_claude_code._http_get", return_value=(0, b"")),
        mock.patch(
            "tokenpak.cli.commands.doctor_claude_code._http_post_json", return_value=(0, b"")
        ),
        mock.patch("tokenpak.cli.commands.doctor_claude_code._get_proxy_pid", return_value=None),
    ):
        fail_count, results = run_claude_code_checks()

    assert len(results) == NUM_CHECKS
    assert isinstance(fail_count, int)
    assert fail_count == sum(1 for r in results if r["status"] == "fail")


# ---------------------------------------------------------------------------
# Test: _normalise_url handles localhost aliases
# ---------------------------------------------------------------------------


def test_normalise_url_localhost():
    assert _normalise_url("http://localhost:8766/") == "http://127.0.0.1:8766"
    assert _normalise_url("http://127.0.0.1:8766") == "http://127.0.0.1:8766"
    assert _normalise_url("http://127.0.0.1:8766/") == "http://127.0.0.1:8766"


# ---------------------------------------------------------------------------
# Test 9: Plugin directory check (CCP-09)
# ---------------------------------------------------------------------------


def test_check9_plugin_dir_missing(tmp_home, monkeypatch):
    """Check 9 fails when neither ~/.claude/plugins/tokenpak nor tokenpak-claude-code exists."""
    result = _check_plugin_dir()
    assert result["status"] == "fail"
    assert "NOT FOUND" in result["message"]
    assert "mkdir -p ~/.claude/plugins/tokenpak" in result["remediation"]


def test_check9_plugin_dir_tokenpak_exists(tmp_home, monkeypatch):
    """Check 9 passes when ~/.claude/plugins/tokenpak exists."""
    plugin_dir = tmp_home / ".claude" / "plugins" / "tokenpak"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    result = _check_plugin_dir()
    assert result["status"] == "pass"
    assert "tokenpak" in result["message"]


def test_check9_plugin_dir_tokenpak_claude_code_exists(tmp_home, monkeypatch):
    """Check 9 passes when ~/.claude/plugins/tokenpak-claude-code exists."""
    plugin_dir = tmp_home / ".claude" / "plugins" / "tokenpak-claude-code"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    result = _check_plugin_dir()
    assert result["status"] == "pass"
    assert "tokenpak-claude-code" in result["message"]


def test_check9_plugin_dir_checks_both_candidates(tmp_home, monkeypatch):
    """Check 9 failure message lists both candidate paths."""
    result = _check_plugin_dir()
    assert result["status"] == "fail"
    assert "tokenpak" in result["message"]
    assert "tokenpak-claude-code" in result["message"] or "tokenpak" in result["detail"]
