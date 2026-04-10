from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tokenpak.cli.commands import exec as exec_mod


def test_builtin_operation_runs(monkeypatch):
    runner = CliRunner()
    called: dict[str, bool] = {"ok": False}

    def fake_validate(params, dry_run):
        called["ok"] = True
        assert dry_run is False
        return 0

    monkeypatch.setitem(exec_mod.BUILTIN_OPERATIONS, "validate-config", fake_validate)

    result = runner.invoke(exec_mod.exec_cmd, ["validate-config"])
    assert result.exit_code == 0, result.output
    assert called["ok"] is True


def test_cleanup_cache_defaults_to_dry_run(monkeypatch):
    runner = CliRunner()
    seen: dict[str, bool] = {"dry": False}

    def fake_cleanup(params, dry_run):
        seen["dry"] = dry_run
        return 0

    monkeypatch.setitem(exec_mod.BUILTIN_OPERATIONS, "cleanup-cache", fake_cleanup)

    result = runner.invoke(exec_mod.exec_cmd, ["cleanup-cache"])
    assert result.exit_code == 0, result.output
    assert seen["dry"] is True
    assert "destructive" in result.output.lower()


def test_macro_json_runs_steps(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    macros = tmp_path / "macros"
    macros.mkdir()

    (macros / "nightly.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"operation": "validate-config", "params": {}},
                    {"operation": "health-check", "params": {}},
                ]
            }
        )
    )

    calls: list[str] = []

    def fake_validate(params, dry_run):
        calls.append("validate-config")
        return 0

    def fake_health(params, dry_run):
        calls.append("health-check")
        return 0

    monkeypatch.setitem(exec_mod.BUILTIN_OPERATIONS, "validate-config", fake_validate)
    monkeypatch.setitem(exec_mod.BUILTIN_OPERATIONS, "health-check", fake_health)

    result = runner.invoke(exec_mod.exec_cmd, ["nightly", "--macros-dir", str(macros)])
    assert result.exit_code == 0, result.output
    assert calls == ["validate-config", "health-check"]


def test_macro_rejects_non_whitelisted_operation(tmp_path: Path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "bad.json").write_text(
        json.dumps({"steps": [{"operation": "rm -rf /", "params": {}}]})
    )

    with pytest.raises(Exception):
        exec_mod.run_macro("bad", macros, dry_run=False)
