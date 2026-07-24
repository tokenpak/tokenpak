"""Settings UI page (/settings/claude-code) — test suite.

Covers:
- Page renders at /settings/claude-code
- Each settings section is present in the response
- Each HTMX POST endpoint persists to a temp env file
- Atomic-write safety (backup created, temp file cleaned up)
- Malformed / invalid input is rejected (422)
- JSON API endpoint returns current settings
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "fastapi",
    reason="fastapi not installed (optional dep — install via tokenpak[serve] or [telemetry])",
)

from fastapi.testclient import TestClient

try:
    from tokenpak.dashboard.app import create_dashboard_app
    from tokenpak.dashboard.settings_persistence import (
        ENV_FILE_PATH,
        load_settings_context,
        read_env_file,
        validate_settings,
        write_settings,
    )
except ImportError as exc:
    pytest.fail(f"Failed to import dashboard / settings_persistence: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app = create_dashboard_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def tmp_env_file(tmp_path: Path) -> Path:
    """Return a fresh temp env file path (does NOT pre-create it)."""
    return tmp_path / "tokenpak.env"


# ---------------------------------------------------------------------------
# 1. Page renders
# ---------------------------------------------------------------------------


class TestSettingsPageRenders:
    def test_returns_200(self, client):
        r = client.get("/settings/claude-code")
        assert r.status_code == 200

    def test_content_type_html(self, client):
        r = client.get("/settings/claude-code")
        assert "text/html" in r.headers.get("content-type", "")

    def test_page_title_present(self, client):
        r = client.get("/settings/claude-code")
        assert "Claude Code Settings" in r.text

    def test_nav_link_present(self, client):
        r = client.get("/settings/claude-code")
        assert "/settings/claude-code" in r.text

    def test_active_profile_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Active Profile" in r.text

    def test_vault_injection_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Vault Injection" in r.text

    def test_budget_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Budget Enforcement" in r.text

    def test_alerts_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Cache Invalidation Alerts" in r.text

    def test_routing_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Provider Routing" in r.text

    def test_local_first_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Local-First Routing" in r.text

    def test_compliance_section(self, client):
        r = client.get("/settings/claude-code")
        assert "Compliance Routing" in r.text

    def test_safety_warning_present(self, client):
        r = client.get("/settings/claude-code")
        assert "Safety warning" in r.text or "safety warning" in r.text.lower()


# ---------------------------------------------------------------------------
# 2. Each toggle / setting persists via HTMX endpoints
# ---------------------------------------------------------------------------


class TestProfilePersists:
    def test_valid_profile_returns_200(self, client, tmp_env_file):
        with patch("tokenpak.dashboard.app.write_settings") as mock_write:
            mock_write.return_value = (True, [])
            r = client.post(
                "/settings/claude-code/htmx/profile",
                data={"profile": "claude-code-tui"},
            )
        assert r.status_code == 200
        assert "claude-code-tui" in r.text

    def test_invalid_profile_rejected(self, client):
        r = client.post(
            "/settings/claude-code/htmx/profile",
            data={"profile": "not-a-real-profile"},
        )
        assert r.status_code == 422

    def test_profile_persistence_writes_correct_key(self, tmp_env_file):
        ok, errors = write_settings(
            {"TOKENPAK_ACTIVE_PROFILE": "claude-code-ide"},
            path=tmp_env_file,
        )
        assert ok, errors
        env = read_env_file(tmp_env_file)
        assert env["TOKENPAK_ACTIVE_PROFILE"] == "claude-code-ide"


class TestVaultSettingsPersist:
    def test_vault_htmx_endpoint_ok(self, client):
        with patch("tokenpak.dashboard.app.write_settings") as mock_write:
            mock_write.return_value = (True, [])
            r = client.post(
                "/settings/claude-code/htmx/vault",
                data={
                    "vault_inject_enabled": "1",
                    "inject_budget": "5000",
                    "inject_top_k": "8",
                    "inject_min_score": "1.5",
                },
            )
        assert r.status_code == 200
        assert "enabled" in r.text.lower()

    def test_vault_persistence_writes_all_keys(self, tmp_env_file):
        ok, errors = write_settings(
            {
                "TOKENPAK_VAULT_INJECT_ENABLED": "1",
                "TOKENPAK_INJECT_BUDGET": "5000",
                "TOKENPAK_INJECT_TOP_K": "8",
                "TOKENPAK_INJECT_MIN_SCORE": "1.5",
            },
            path=tmp_env_file,
        )
        assert ok, errors
        env = read_env_file(tmp_env_file)
        assert env["TOKENPAK_VAULT_INJECT_ENABLED"] == "1"
        assert env["TOKENPAK_INJECT_BUDGET"] == "5000"
        assert env["TOKENPAK_INJECT_TOP_K"] == "8"
        assert env["TOKENPAK_INJECT_MIN_SCORE"] == "1.5"


class TestBudgetSettingsPersist:
    def test_budget_htmx_endpoint_ok(self, client):
        with patch("tokenpak.dashboard.app.write_settings") as mock_write:
            mock_write.return_value = (True, [])
            r = client.post(
                "/settings/claude-code/htmx/budget",
                data={"budget_controller_enabled": "1", "budget_total": "20000"},
            )
        assert r.status_code == 200

    def test_budget_persistence_writes_correct_keys(self, tmp_env_file):
        ok, errors = write_settings(
            {"TOKENPAK_BUDGET_CONTROLLER": "1", "TOKENPAK_BUDGET_TOTAL": "20000"},
            path=tmp_env_file,
        )
        assert ok, errors
        env = read_env_file(tmp_env_file)
        assert env["TOKENPAK_BUDGET_CONTROLLER"] == "1"
        assert env["TOKENPAK_BUDGET_TOTAL"] == "20000"


class TestAlertSettingsPersist:
    def test_alerts_htmx_endpoint_ok(self, client):
        with patch("tokenpak.dashboard.app.write_settings") as mock_write:
            mock_write.return_value = (True, [])
            r = client.post(
                "/settings/claude-code/htmx/alerts",
                data={
                    "cache_alert_webhook_enabled": "1",
                    "cache_alert_threshold": "30",
                },
            )
        assert r.status_code == 200

    def test_alerts_htmx_rejects_webhook_url_post(self, client):
        r = client.post(
            "/settings/claude-code/htmx/alerts",
            data={
                "cache_alert_webhook_enabled": "1",
                "cache_alert_webhook_url": "https://example.com/hook",
                "cache_alert_threshold": "30",
            },
        )
        assert r.status_code == 422
        assert "Webhook URL cannot be saved here" in r.text

    def test_alerts_htmx_rejects_slack_destination_post(self, client):
        r = client.post(
            "/settings/claude-code/htmx/alerts",
            data={
                "cache_alert_webhook_enabled": "1",
                "cache_alert_slack_channel": "#tokenpak-alerts",
                "cache_alert_threshold": "30",
            },
        )
        assert r.status_code == 422
        assert "Slack destination cannot be saved here" in r.text

    def test_alerts_page_marks_slack_destination_read_only(self, client):
        r = client.get("/settings/claude-code")
        assert r.status_code == 200
        assert "Slack destinations are read-only here" in r.text
        assert 'name="cache_alert_slack_channel"' not in r.text


# ---------------------------------------------------------------------------
# 3. Atomic-write safety
# ---------------------------------------------------------------------------


class TestAtomicWriteSafety:
    def test_write_creates_file(self, tmp_env_file):
        assert not tmp_env_file.exists()
        ok, errors = write_settings(
            {"TOKENPAK_ACTIVE_PROFILE": "claude-code-cli"},
            path=tmp_env_file,
        )
        assert ok, errors
        assert tmp_env_file.exists()

    def test_write_leaves_no_tmp_file(self, tmp_env_file):
        write_settings({"TOKENPAK_ACTIVE_PROFILE": "claude-code-sdk"}, path=tmp_env_file)
        tmp = tmp_env_file.with_suffix(".tmp")
        assert not tmp.exists()

    def test_write_creates_backup_on_existing_file(self, tmp_env_file):
        tmp_env_file.write_text("TOKENPAK_PORT=8766\n")
        write_settings({"TOKENPAK_ACTIVE_PROFILE": "claude-code-cli"}, path=tmp_env_file)
        bak_files = list(tmp_env_file.parent.glob("*.bak.*"))
        assert len(bak_files) >= 1

    def test_write_preserves_existing_keys(self, tmp_env_file):
        tmp_env_file.write_text("TOKENPAK_PORT=8766\nTOKENPAK_MODE=hybrid\n")
        write_settings({"TOKENPAK_ACTIVE_PROFILE": "claude-code-tui"}, path=tmp_env_file)
        env = read_env_file(tmp_env_file)
        assert env["TOKENPAK_PORT"] == "8766"
        assert env["TOKENPAK_MODE"] == "hybrid"
        assert env["TOKENPAK_ACTIVE_PROFILE"] == "claude-code-tui"

    def test_write_updates_existing_key_in_place(self, tmp_env_file):
        tmp_env_file.write_text(
            "# TokenPak config\nTOKENPAK_BUDGET_TOTAL=12000\nTOKENPAK_MODE=hybrid\n"
        )
        write_settings({"TOKENPAK_BUDGET_TOTAL": "25000"}, path=tmp_env_file)
        content = tmp_env_file.read_text()
        # Only one occurrence of the key
        assert content.count("TOKENPAK_BUDGET_TOTAL") == 1
        assert "25000" in content
        # Comment preserved
        assert "# TokenPak config" in content


# ---------------------------------------------------------------------------
# 4. Malformed input rejected
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_negative_budget_rejected(self):
        errors = validate_settings({"TOKENPAK_INJECT_BUDGET": "-1"})
        assert errors

    def test_non_numeric_top_k_rejected(self):
        errors = validate_settings({"TOKENPAK_INJECT_TOP_K": "abc"})
        assert errors

    def test_non_numeric_min_score_rejected(self):
        errors = validate_settings({"TOKENPAK_INJECT_MIN_SCORE": "xyz"})
        assert errors

    def test_invalid_profile_rejected(self):
        errors = validate_settings({"TOKENPAK_ACTIVE_PROFILE": "not-a-profile"})
        assert errors

    def test_invalid_bool_rejected(self):
        errors = validate_settings({"TOKENPAK_BUDGET_CONTROLLER": "maybe"})
        assert errors

    def test_pct_over_100_rejected(self):
        errors = validate_settings({"TOKENPAK_CACHE_ALERT_THRESHOLD": "101"})
        assert errors

    def test_negative_pct_rejected(self):
        errors = validate_settings({"TOKENPAK_CACHE_ALERT_THRESHOLD": "-5"})
        assert errors

    def test_valid_settings_no_errors(self):
        errors = validate_settings(
            {
                "TOKENPAK_ACTIVE_PROFILE": "claude-code-cli",
                "TOKENPAK_INJECT_BUDGET": "4000",
                "TOKENPAK_INJECT_TOP_K": "5",
                "TOKENPAK_INJECT_MIN_SCORE": "2.0",
                "TOKENPAK_BUDGET_CONTROLLER": "1",
                "TOKENPAK_BUDGET_TOTAL": "12000",
                "TOKENPAK_CACHE_ALERT_THRESHOLD": "50.0",
            }
        )
        assert errors == []

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("ANTHROPIC_API_KEY", "sk-ant-test"),
            ("OPENAI_API_KEY", "sk-test"),
            ("TOKENPAK_REMOTE_HOST", "https://remote.example.invalid"),
            ("TOKENPAK_OLLAMA_UPSTREAM", "https://ollama.example.invalid"),
            ("TOKENPAK_CACHE_ALERT_WEBHOOK_URL", "https://example.com/hook"),
            ("TOKENPAK_CACHE_ALERT_SLACK_CHANNEL", "#tokenpak-alerts"),
        ],
    )
    def test_sensitive_remote_excluded_keys_rejected_by_validation(self, key, value):
        errors = validate_settings({key: value})
        assert errors
        assert "dashboard writes are disabled" in errors[0]

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("ANTHROPIC_API_KEY", "sk-ant-test"),
            ("OPENAI_API_KEY", "sk-test"),
            ("TOKENPAK_REMOTE_HOST", "https://remote.example.invalid"),
            ("TOKENPAK_OLLAMA_UPSTREAM", "https://ollama.example.invalid"),
            ("TOKENPAK_CACHE_ALERT_WEBHOOK_URL", "https://example.com/hook"),
            ("TOKENPAK_CACHE_ALERT_SLACK_CHANNEL", "#tokenpak-alerts"),
        ],
    )
    def test_sensitive_remote_excluded_keys_abort_without_creating_file(
        self, tmp_env_file, key, value
    ):
        ok, errors = write_settings({key: value}, path=tmp_env_file)
        assert not ok
        assert errors
        assert not tmp_env_file.exists()

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("ANTHROPIC_API_KEY", "sk-ant-test"),
            ("OPENAI_API_KEY", "sk-test"),
            ("TOKENPAK_REMOTE_HOST", "https://remote.example.invalid"),
            ("TOKENPAK_OLLAMA_UPSTREAM", "https://ollama.example.invalid"),
            ("TOKENPAK_CACHE_ALERT_WEBHOOK_URL", "https://example.com/hook"),
            ("TOKENPAK_CACHE_ALERT_SLACK_CHANNEL", "#tokenpak-alerts"),
        ],
    )
    def test_sensitive_remote_excluded_keys_rejected_even_with_skip_validation(
        self, tmp_env_file, key, value
    ):
        ok, errors = write_settings({key: value}, path=tmp_env_file, skip_validation=True)
        assert not ok
        assert errors
        assert not tmp_env_file.exists()

    def test_write_aborts_on_invalid_input(self, tmp_env_file):
        ok, errors = write_settings(
            {"TOKENPAK_INJECT_BUDGET": "not-a-number"},
            path=tmp_env_file,
        )
        assert not ok
        assert errors
        assert not tmp_env_file.exists()


# ---------------------------------------------------------------------------
# 5. JSON API endpoint
# ---------------------------------------------------------------------------


class TestApiCurrentSettings:
    def test_returns_200(self, client):
        r = client.get("/settings/claude-code/api/current")
        assert r.status_code == 200

    def test_returns_json(self, client):
        r = client.get("/settings/claude-code/api/current")
        assert "application/json" in r.headers.get("content-type", "")

    def test_has_expected_keys(self, client):
        r = client.get("/settings/claude-code/api/current")
        data = r.json()
        for key in (
            "active_profile",
            "vault_inject_enabled",
            "inject_budget",
            "inject_top_k",
            "inject_min_score",
            "budget_controller_enabled",
            "budget_total",
            "cache_alert_webhook_enabled",
            "cache_alert_threshold",
            "local_first_routing_enabled",
            "env_file_path",
        ):
            assert key in data, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# 6. Read env file
# ---------------------------------------------------------------------------


class TestReadEnvFile:
    def test_missing_file_returns_empty_dict(self, tmp_env_file):
        assert read_env_file(tmp_env_file) == {}

    def test_comments_skipped(self, tmp_env_file):
        tmp_env_file.write_text("# comment\nKEY=value\n")
        env = read_env_file(tmp_env_file)
        assert env == {"KEY": "value"}

    def test_blank_lines_skipped(self, tmp_env_file):
        tmp_env_file.write_text("\n\nKEY=value\n\n")
        env = read_env_file(tmp_env_file)
        assert env == {"KEY": "value"}

    def test_duplicate_key_last_wins(self, tmp_env_file):
        tmp_env_file.write_text("KEY=first\nKEY=second\n")
        env = read_env_file(tmp_env_file)
        assert env["KEY"] == "second"
