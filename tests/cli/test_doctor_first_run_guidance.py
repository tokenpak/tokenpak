# SPDX-License-Identifier: Apache-2.0
"""Focused doctor copy contracts for first-run guidance."""

from __future__ import annotations

import json
import re
from io import StringIO
from unittest.mock import patch

from tokenpak.cli.commands import doctor


class _ClosedSocket:
    def settimeout(self, _timeout: float) -> None:
        return None

    def connect_ex(self, _addr: tuple[str, int]) -> int:
        return 111

    def close(self) -> None:
        return None


def _prepare_doctor(monkeypatch, tmp_path) -> None:
    fake_home = tmp_path / "home"
    tokenpak_home = fake_home / ".tpk"
    tokenpak_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("TOKENPAK_HOME", str(tokenpak_home))
    monkeypatch.setattr(doctor, "_route_state", lambda: ("not routed", None))
    monkeypatch.setattr(doctor, "_update_state", lambda: ("unknown", None))
    monkeypatch.setattr(doctor, "_proxy_state", lambda: "stopped")
    monkeypatch.setattr(doctor, "_proxy_get", lambda *_a, **_k: None)
    monkeypatch.setattr(doctor.socket, "socket", lambda *_a, **_k: _ClosedSocket())
    monkeypatch.setattr("tokenpak.creds.doctor.run", lambda *a, **k: [])


def _doctor_json(monkeypatch, tmp_path) -> dict:
    _prepare_doctor(monkeypatch, tmp_path)
    captured = StringIO()
    with patch("sys.stdout", captured):
        doctor.run_doctor(output_json=True)
    text = captured.getvalue()
    start = text.rfind("\n{")
    return json.loads(text[start + 1 :] if start != -1 else text)


def _doctor_human(monkeypatch, tmp_path) -> str:
    _prepare_doctor(monkeypatch, tmp_path)
    captured = StringIO()
    with patch("sys.stdout", captured):
        doctor.run_doctor()
    return captured.getvalue()


def _find_check(payload: dict, name: str) -> dict:
    for check in payload["checks"]:
        if check["check"] == name:
            return check
    raise AssertionError(f"{name} check missing")


def _parser_verbs() -> set[str]:
    """Every subcommand the shipped parser exposes."""
    from tokenpak._cli_core import build_parser

    names: set[str] = set()
    for action in build_parser()._actions:
        if getattr(action, "choices", None):
            names |= set(action.choices)
    return names


def test_lifecycle_summary_hints_name_commands_the_parser_exposes():
    """Every hint must be a runnable command, and the right one.

    This asserted the literal "Run: tokenpak restart". The guarantee it was
    protecting is that the hint names a verb that actually exists — the bug
    was hints like `tokenpak proxy restart`, which the parser has never had.
    Pinning the exact string also pinned the wrong advice: a proxy in the
    `stopped` state has never been started, so `restart` was the wrong verb
    to suggest. Derive the check from the live parser instead.
    """
    out = doctor.build_lifecycle_summary(
        version="1.0.0",
        setup_present=True,
        route_state="active",
        proxy_state="stopped",
        update_state="current",
        update_latest=None,
    )

    verbs = _parser_verbs()
    hinted = re.findall(r"Run: tokenpak ([a-z-]+)", out)
    assert hinted, f"stopped proxy should carry a next-step hint:\n{out}"
    for verb in hinted:
        assert verb in verbs, f"hint names `tokenpak {verb}`, which the parser does not expose"

    # A stopped proxy is started, not restarted.
    assert "Run: tokenpak start" in out, out
    assert "tokenpak proxy " + "restart" not in out


def test_api_key_setup_detail_includes_windows_and_posix_examples():
    detail = doctor._api_key_setup_detail()
    assert "no direct API key" in detail
    assert "export ANTHROPIC_API_KEY=sk-..." in detail
    assert 'setx ANTHROPIC_API_KEY "sk-..."' in detail
    assert "set ANTHROPIC_API_KEY=sk-..." in detail


def test_doctor_treats_missing_api_keys_as_optional_for_oauth(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    check = _find_check(_doctor_json(monkeypatch, tmp_path), "api_keys")

    assert check["status"] == "pass"
    assert "optional" in check["message"]
    assert "OAuth/session auth" in check["message"]
    assert "direct provider key" in check["detail"]


def test_doctor_json_exposes_partial_custom_provider_registration(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_custom_provider_counts", lambda: (2, 1, ""))

    payload = _doctor_json(monkeypatch, tmp_path)
    check = _find_check(payload, "custom_providers")

    assert payload["custom_providers"] == {
        "configured": 2,
        "registered": 1,
        "error": None,
    }
    assert check["status"] == "warn"
    assert "1/2 registered" in check["message"]
    assert "1 configured provider(s) were skipped" in check["detail"]


def test_doctor_human_exposes_custom_provider_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_custom_provider_counts", lambda: (2, 1, ""))

    output = _doctor_human(monkeypatch, tmp_path)

    assert "Custom providers    1/2 registered" in output


def test_disk_usage_probe_stops_at_entry_limit(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    for i in range(5):
        (root / f"file-{i}.txt").write_text("x")

    result = doctor._measure_disk_usage(root, max_entries=3, timeout_s=60.0)

    assert result.truncated is True
    assert result.reason == "entry limit 3"
    assert result.entries == 3


def test_doctor_reports_bounded_disk_usage_as_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(
        doctor,
        "_measure_disk_usage",
        lambda *_a, **_k: doctor._DiskUsageResult(
            total_bytes=2048,
            files=2,
            entries=7,
            truncated=True,
            reason="timeout 0.25s",
        ),
    )

    check = _find_check(_doctor_json(monkeypatch, tmp_path), "disk_usage")

    assert check["status"] == "warn"
    assert "bounded after 7 entries" in check["message"]
    assert "timeout 0.25s" in check["message"]
    assert "tokenpak maintenance" in check["message"]


def test_proxy_health_hint_names_a_real_command(monkeypatch, tmp_path):
    """Same guarantee as the lifecycle panel, on the proxy_health check."""
    check = _find_check(_doctor_json(monkeypatch, tmp_path), "proxy_health")
    message = check["message"]

    hinted = re.findall(r"tokenpak ([a-z-]+)", message)
    assert hinted, f"proxy_health should name a next step: {message!r}"
    verbs = _parser_verbs()
    for verb in hinted:
        assert verb in verbs, f"hint names `tokenpak {verb}`, not a parser command"

    # A proxy that is not running is started, not restarted.
    assert "tokenpak start" in message, message
    assert "tokenpak proxy " + "restart" not in message
