"""CLI contracts for deterministic process-local memory optimization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tokenpak.cli.commands._config_optimize import cmd_config_optimize
from tokenpak.services import memory_optimization as optimizer


def _facts(memory_mib: int = 4096) -> optimizer.HostFacts:
    return optimizer.HostFacts(
        platform="linux",
        cpu_count=4,
        physical_memory_bytes=memory_mib * optimizer.MIB,
        cgroup_memory_limit_bytes=None,
        effective_memory_bytes=memory_mib * optimizer.MIB,
        memory_limit_source="physical",
    )


def _args(action: str, **overrides):
    values = {
        "optimize_action": action,
        "profile": "balanced",
        "mode": "auto",
        "expect_hash": None,
        "force": False,
        "json": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def isolated_optimizer(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    for name in list(os.environ):
        if name.startswith("TOKENPAK_MEMORY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(optimizer, "probe_host_facts", lambda: _facts())
    return tmp_path


def _json_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def test_plan_is_default_read_only_json(isolated_optimizer, capsys) -> None:
    rc = cmd_config_optimize(_args("plan"))
    payload = _json_stdout(capsys)
    assert rc == optimizer.EXIT_OK
    assert payload["action"] == "plan"
    assert payload["plan"]["scope"] == "process"
    assert payload["plan"]["memory_guard"]["enabled"] is True
    assert len(payload["plan_sha256"]) == 64
    assert list(isolated_optimizer.iterdir()) == []


def test_apply_status_and_rollback_json(isolated_optimizer, capsys) -> None:
    assert cmd_config_optimize(_args("apply")) == optimizer.EXIT_OK
    applied = _json_stdout(capsys)
    assert applied["changed"] is True
    assert applied["action"] == "apply"

    assert cmd_config_optimize(_args("status")) == optimizer.EXIT_OK
    status = _json_stdout(capsys)
    assert status["status"]["state"] == "clean"
    assert status["status"]["config"]["plan_sha256"] == applied["plan_sha256"]

    assert cmd_config_optimize(_args("rollback")) == optimizer.EXIT_OK
    rolled_back = _json_stdout(capsys)
    assert rolled_back["result"]["restored"] == "absent"
    assert not optimizer.managed_paths(isolated_optimizer).config.exists()


def test_unsupported_plan_returns_pinned_exit_two(isolated_optimizer, monkeypatch, capsys) -> None:
    monkeypatch.setattr(optimizer, "probe_host_facts", lambda: _facts(256))
    rc = cmd_config_optimize(_args("plan", profile="conservative"))
    payload = _json_stdout(capsys)
    assert rc == optimizer.EXIT_UNSUPPORTED == 2
    assert payload["plan"]["supported"] is False


def test_expect_hash_mismatch_returns_apply_refused(isolated_optimizer, capsys) -> None:
    rc = cmd_config_optimize(_args("apply", expect_hash="0" * 64))
    payload = _json_stdout(capsys)
    assert rc == optimizer.EXIT_APPLY_REFUSED == 3
    assert payload["exit_code"] == 3
    assert "expect-hash" in payload["error"]


def test_drifted_rollback_requires_explicit_force(isolated_optimizer, capsys) -> None:
    assert cmd_config_optimize(_args("apply")) == 0
    capsys.readouterr()
    paths = optimizer.managed_paths(isolated_optimizer)
    paths.config.write_text("external drift\n")

    assert cmd_config_optimize(_args("rollback")) == optimizer.EXIT_ROLLBACK_REFUSED
    refused = _json_stdout(capsys)
    assert "--force" in refused["error"]

    assert cmd_config_optimize(_args("rollback", force=True)) == optimizer.EXIT_OK
    forced = _json_stdout(capsys)
    assert forced["result"]["restored"] == "absent"


def test_corrupt_status_returns_pinned_exit_five(isolated_optimizer, capsys) -> None:
    optimizer.managed_paths(isolated_optimizer).config.write_text("bad-json")
    rc = cmd_config_optimize(_args("status"))
    payload = _json_stdout(capsys)
    assert rc == optimizer.EXIT_CORRUPT == 5
    assert payload["status"]["state"] == "corrupt_config"


@pytest.mark.parametrize(
    ("action", "overrides", "message"),
    [
        ("plan", {"force": True}, "--force"),
        ("status", {"expect_hash": "0" * 64}, "--expect-hash"),
    ],
)
def test_action_specific_flags_are_refused(
    isolated_optimizer, capsys, action, overrides, message
) -> None:
    rc = cmd_config_optimize(_args(action, **overrides))
    payload = _json_stdout(capsys)
    assert rc == optimizer.EXIT_APPLY_REFUSED
    assert message in payload["error"]


def test_real_parser_exposes_optimize_help(tmp_path) -> None:
    env = os.environ.copy()
    env["TOKENPAK_HOME"] = str(tmp_path)
    for name in list(env):
        if name.startswith("TOKENPAK_MEMORY_"):
            env.pop(name)
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "config", "optimize", "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--plan" in result.stdout
    assert "--apply" in result.stdout
    assert "--status" in result.stdout
    assert "--rollback" in result.stdout
    assert "process-local" in result.stdout


def test_real_parser_plan_json_is_read_only(tmp_path) -> None:
    home = tmp_path / "home"
    env = os.environ.copy()
    env["TOKENPAK_HOME"] = str(home)
    for name in list(env):
        if name.startswith("TOKENPAK_MEMORY_"):
            env.pop(name)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tokenpak",
            "config",
            "optimize",
            "--plan",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode in {optimizer.EXIT_OK, optimizer.EXIT_UNSUPPORTED}
    json_start = result.stdout.find("{")
    assert json_start >= 0, result.stdout
    payload = json.loads(result.stdout[json_start:])
    assert payload["action"] == "plan"
    assert payload["plan"]["scope"] == "process"
    assert not home.exists()
