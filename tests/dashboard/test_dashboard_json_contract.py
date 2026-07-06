# SPDX-License-Identifier: Apache-2.0
"""Dashboard v2 JSON contract and compatibility tests."""

from __future__ import annotations

from tokenpak.cli.commands import dashboard
from tokenpak.platform.capabilities import _detect_dashboard_capabilities


def test_dashboard_json_contract_reports_unavailable_sources_as_unknown(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setattr(dashboard, "_http_get", lambda *args, **kwargs: None)

    payload = dashboard.collect_dashboard_snapshot()

    assert payload["schema_version"] == dashboard.SCHEMA_VERSION
    for key in (
        "generated_at",
        "capabilities",
        "sources",
        "summary",
        "dispatch",
        "companion",
        "spend",
        "debug",
        "warnings",
    ):
        assert key in payload

    assert payload["summary"]["proxy"]["state"] == "unknown"
    assert payload["summary"]["requests"] == {
        "state": "not_measured",
        "value": None,
        "source": "proxy_stats",
        "unit": "count",
    }
    assert payload["spend"]["saved_usd"]["state"] == "not_measured"
    assert payload["spend"]["saved_usd"]["value"] is None
    assert payload["capabilities"]["fleet_projection"]["default_enabled"] is False
    assert payload["debug"]["tokenpak_home"] == str(tmp_path)


def test_dashboard_json_contract_preserves_measured_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))

    responses = {
        "/health": {"status": "ok"},
        "/stats": {
            "requests": 0,
            "errors": 0,
            "saved_tokens": 0,
            "saved_dollars": 0.0,
            "compression_ratio": 0.5,
        },
        "/stats/session": None,
        "/degradation": {"recent_events": []},
    }
    monkeypatch.setattr(
        dashboard,
        "_http_get",
        lambda path, *args, **kwargs: responses.get(path),
    )

    payload = dashboard.collect_dashboard_snapshot()

    assert payload["summary"]["proxy"]["state"] == "running"
    assert payload["summary"]["requests"]["state"] == "measured"
    assert payload["summary"]["requests"]["value"] == 0
    assert payload["spend"]["saved_usd"]["state"] == "measured"
    assert payload["spend"]["saved_usd"]["value"] == 0.0
    assert payload["spend"]["compression_percent"]["value"] == 50.0


def test_dashboard_paths_follow_tokenpak_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))

    assert dashboard._auth_profiles_file() == tmp_path / "auth-profiles.json"
    assert dashboard._fleet_config_file() == tmp_path / "fleet.yaml"
    assert dashboard._proxy_pid_file() == tmp_path / "proxy.pid"


def test_dashboard_capability_detection_has_cross_platform_fallback(tmp_path) -> None:
    capabilities = _detect_dashboard_capabilities(
        stdout_is_tty=False,
        os_name="nt",
        sys_platform="win32",
        proc_path=tmp_path / "missing-proc",
    )

    assert capabilities["terminal_ui"]["state"] == "not_interactive"
    assert capabilities["process_inspection"]["state"] == "unsupported"
    assert capabilities["fleet_projection"]["state"] == "not_configured"
