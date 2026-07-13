# SPDX-License-Identifier: Apache-2.0
"""Selected-CODEX_HOME provisioning surfaces never fall back to live state."""

from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path

import pytest

from tokenpak.cli.commands import uninstall as top_level_uninstall
from tokenpak.companion.codex import (
    agents_md,
    doctor,
    hooks,
    mcp_config,
    session_home,
    state_lock,
    uninstall,
)
from tokenpak.companion.codex import skills_installer as skills
from tokenpak.companion.codex.session_home import select_paths


@pytest.fixture(autouse=True)
def _isolated_user_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "test-user-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("TOKENPAK_CODEX_SESSION_MODE", raising=False)


def _fake_bundled(tmp_path: Path) -> Path:
    bundled = tmp_path / "bundled"
    for name in ("alpha", "beta"):
        skill = bundled / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n")
    return bundled


def test_hooks_and_agents_install_into_selected_home(tmp_path: Path):
    selected = tmp_path / "selected-codex"
    other = tmp_path / "other-codex"
    other.mkdir()

    hooks_path = hooks._install_hooks(codex_home=selected)
    agents_path = agents_md._install_agents_md(codex_home=selected)

    assert hooks_path == selected / "hooks.json"
    assert agents_path == selected / "AGENTS.md"
    assert hooks_path.is_file()
    assert agents_path.is_file()
    assert list(other.iterdir()) == []


def test_selected_home_plumbing_does_not_expand_public_signatures():
    assert tuple(inspect.signature(agents_md.install_agents_md).parameters) == ("target",)
    assert tuple(inspect.signature(hooks.install_hooks).parameters) == ("target",)
    assert tuple(inspect.signature(hooks.ensure_hooks_feature_enabled).parameters) == ()
    assert tuple(inspect.signature(mcp_config.is_registered).parameters) == ()
    assert tuple(inspect.signature(mcp_config.register).parameters) == ("env_vars",)
    assert tuple(inspect.signature(mcp_config.unregister).parameters) == ()
    assert tuple(inspect.signature(uninstall.remove_mcp).parameters) == ()


def test_codex_commands_receive_selected_home(monkeypatch, tmp_path: Path):
    selected = tmp_path / "selected-codex"
    calls: list[tuple[list[str], dict[str, str]]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return Result()

    monkeypatch.setattr(mcp_config.subprocess, "run", fake_run)
    monkeypatch.setattr(hooks.subprocess, "run", fake_run)

    assert mcp_config._is_registered(selected)
    assert mcp_config._unregister(selected)
    assert hooks._ensure_hooks_feature_enabled(selected)

    assert calls
    assert all(env["CODEX_HOME"] == str(selected) for _, env in calls)
    assert (selected / "config.toml").is_file()


def test_mcp_registration_writes_selected_config(monkeypatch, tmp_path: Path):
    selected = tmp_path / "selected-codex"
    calls: list[tuple[list[str], dict[str, str]]] = []

    class Result:
        stdout = ""
        stderr = ""

        def __init__(self, returncode: int):
            self.returncode = returncode

    results = iter((Result(1), Result(0)))

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return next(results)

    monkeypatch.setattr(mcp_config.subprocess, "run", fake_run)

    assert mcp_config._register({"TOKENPAK_COMPANION_PROFILE": "test"}, codex_home=selected)
    assert calls[0][0][:3] == ["codex", "mcp", "get"]
    assert calls[1][0][:3] == ["codex", "mcp", "add"]
    assert all(env["CODEX_HOME"] == str(selected) for _, env in calls)


def test_skills_payload_is_global_but_selected_configs_reference_it(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    root = tmp_path / "home" / ".agents" / "skills"
    first = tmp_path / "selected-one" / "config.toml"
    second = tmp_path / "selected-two" / "config.toml"
    first.parent.mkdir(parents=True)
    first.write_text('model = "gpt-test"\n')

    installed = skills.install_skills(root)
    first_refs = skills._configure_skills(first, skills_root=root)
    second_refs = skills._configure_skills(second, skills_root=root)

    assert first_refs == installed
    assert second_refs == installed
    assert skills._configured_skill_paths(first) == installed
    assert skills._configured_skill_paths(second) == installed
    assert 'model = "gpt-test"' in first.read_text()
    assert all(path.parent == root for path in installed)
    assert not (first.parent / "skills").exists()
    assert not (second.parent / "skills").exists()


def test_skills_config_is_idempotent_and_excludes_runtime_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    root = tmp_path / ".agents" / "skills"
    config = tmp_path / "codex-home" / "config.toml"
    skills.install_skills(root)
    for forbidden in (
        "state_5.sqlite",
        "logs_2.sqlite",
        "state_5.sqlite-wal",
        "logs_2.sqlite-shm",
        "history.jsonl",
    ):
        (tmp_path / forbidden).write_text("must remain outside selected home")

    skills._configure_skills(config, skills_root=root)
    once = config.read_text()
    skills._configure_skills(config, skills_root=root)

    assert config.read_text() == once
    assert config.read_text().count(skills._SKILLS_CONFIG_BEGIN) == 1
    assert not any(name in config.read_text() for name in (".sqlite", "history.jsonl"))
    assert set(config.parent.iterdir()) == {config}


@pytest.mark.parametrize("original", ["", 'model = "gpt-test"', 'model = "gpt-test"\n'])
def test_skills_config_cleanup_restores_original_bytes(monkeypatch, tmp_path: Path, original: str):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    root = tmp_path / ".agents" / "skills"
    config = tmp_path / "codex-home" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(original, encoding="utf-8")
    skills.install_skills(root)

    skills._configure_skills(config, skills_root=root)
    assert skills._clean_skills_config(config)

    assert config.read_text(encoding="utf-8") == original


def test_default_skill_roots_follow_runtime_home(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(skills, "_DEFAULT_TARGET", None)
    monkeypatch.setattr(skills, "_LEGACY_TARGET", None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert skills._default_skills_root() == tmp_path / ".agents" / "skills"
    assert skills._legacy_skills_root() == tmp_path / ".codex" / "skills"


def test_doctor_real_checks_report_and_use_every_selected_path(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    selected = tmp_path / "selected-codex"
    workspace = tmp_path / "project"
    paths = select_paths("workspace", workspace_dir=workspace, selected_home=selected)
    selected.mkdir(parents=True)
    hooks._install_hooks(codex_home=selected)
    agents_md._install_agents_md(codex_home=selected)
    skills.install_skills(paths.skills_root)
    skills._configure_skills(paths.config, skills_root=paths.skills_root)

    class Result:
        returncode = 0
        stdout = "hooks under development true\n"
        stderr = ""

    seen_envs = []

    def fake_run(command, **kwargs):
        seen_envs.append(kwargs["env"])
        return Result()

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        [
            ("hooks feature", doctor.check_hooks_feature),
            ("MCP registration", doctor.check_mcp_registered),
            ("hooks.json schema", doctor.check_hooks_json),
            ("AGENTS.md", doctor.check_agents_md),
            ("skills installed", doctor.check_skills_installed),
            ("skills selected config", doctor._check_skills_config),
        ],
    )
    assert doctor._run_selected(paths=paths) == 0
    output = capsys.readouterr().out

    expected = {
        "session mode": "workspace",
        "workspace": str(paths.workspace),
        "CODEX_HOME": str(selected),
        "source home": str(paths.source_home),
        "config": str(selected / "config.toml"),
        "auth": str(selected / "auth.json"),
        "MCP config": str(selected / "config.toml"),
        "hooks": str(selected / "hooks.json"),
        "AGENTS.md": str(selected / "AGENTS.md"),
        "skills": str(paths.skills_root),
        "PID sentinel": str(selected / "codex.pid"),
    }
    for label, value in expected.items():
        assert f"{label}: {value}" in output
    assert seen_envs
    assert all(env["CODEX_HOME"] == str(selected) for env in seen_envs)
    assert not (Path.home() / ".codex").exists()


def test_doctor_reports_isolated_orphans_and_disk_usage(monkeypatch, tmp_path: Path):
    tokenpak_home = tmp_path / "tokenpak-home"
    monkeypatch.setenv("TOKENPAK_HOME", str(tokenpak_home))
    source = Path.home() / ".codex"
    source.mkdir()
    paths = select_paths(
        "isolated",
        session_id="doctor-orphan",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    session_home.provision(paths)
    old = time.time() - 120
    os.utime(paths.home, (old, old))
    token = doctor._ACTIVE_RETENTION_REPORT.set(None)
    try:
        orphan_status, orphan_detail = doctor._check_orphaned_codex_homes()
        disk_status, disk_detail = doctor._check_codex_home_disk_usage()
    finally:
        doctor._ACTIVE_RETENTION_REPORT.reset(token)

    assert orphan_status == doctor._WARN
    assert "orphaned=1" in orphan_detail
    assert disk_status is True
    assert "500 MB" in disk_detail


def test_uninstall_cleans_only_selected_home_artifacts(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    root = Path.home() / ".agents" / "skills"
    selected = tmp_path / "selected-codex"
    other = tmp_path / "other-codex"
    paths = select_paths("workspace", selected_home=selected)
    other.mkdir(parents=True)

    hooks._install_hooks(codex_home=selected)
    agents_md._install_agents_md(codex_home=selected)
    skills.install_skills(root)
    skills._configure_skills(paths.config, skills_root=root)
    other_config = other / "config.toml"
    skills._configure_skills(other_config, skills_root=root)
    (other / "hooks.json").write_text(json.dumps({"keep": True}))
    (other / "AGENTS.md").write_text("# Keep\n")
    selected.chmod(0o700)

    monkeypatch.setattr(uninstall, "_remove_mcp", lambda codex_home=None: True)
    monkeypatch.setattr(uninstall, "clean_rates_snapshot", lambda: (False, "absent"))

    assert uninstall._run_selected(paths=paths) == 0
    assert not paths.hooks.exists()
    assert not paths.agents.exists()
    assert skills._configured_skill_paths(paths.config) == []
    assert (other / "hooks.json").is_file()
    assert (other / "AGENTS.md").is_file()
    assert skills._configured_skill_paths(other_config)
    assert all(path.is_dir() for path in skills._configured_skill_paths(other_config))


def test_explicit_global_skill_removal_preserves_unrelated_skills(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    canonical = Path.home() / ".agents" / "skills"
    legacy = Path.home() / ".codex" / "skills"
    skills.install_skills(canonical)
    skills.install_skills(legacy)
    unrelated = canonical / "unrelated"
    unrelated.mkdir()
    (unrelated / "SKILL.md").write_text("# unrelated\n")
    paths = select_paths("workspace", selected_home=tmp_path / "selected-codex")
    monkeypatch.setattr(uninstall, "_remove_mcp", lambda codex_home=None: True)
    monkeypatch.setattr(uninstall, "clean_rates_snapshot", lambda: (False, "absent"))

    assert uninstall._run_selected(paths=paths, remove_global_skills=True) == 0
    assert unrelated.is_dir()
    assert skills.list_installed_skills(canonical) == []
    assert skills.list_installed_skills(legacy) == []


def test_shared_uninstall_removes_global_skills_when_no_managed_home_references_exist(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    canonical = Path.home() / ".agents" / "skills"
    skills.install_skills(canonical)
    selected = Path.home() / ".codex"
    paths = select_paths("shared", selected_home=selected)
    monkeypatch.setattr(uninstall, "_remove_mcp", lambda codex_home=None: True)
    monkeypatch.setattr(uninstall, "clean_rates_snapshot", lambda: (False, "absent"))

    assert uninstall._run_selected(paths=paths) == 0
    assert skills.list_installed_skills(canonical) == []


def test_shared_uninstall_preserves_global_skills_referenced_by_workspace_home(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(skills, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    canonical = Path.home() / ".agents" / "skills"
    skills.install_skills(canonical)
    tokenpak_home = Path.home() / ".tokenpak"
    workspace = select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=Path.home() / ".codex",
    )
    session_home.provision(workspace)
    selected = select_paths("shared", selected_home=Path.home() / ".codex")
    monkeypatch.setattr(uninstall, "_remove_mcp", lambda codex_home=None: True)
    monkeypatch.setattr(uninstall, "clean_rates_snapshot", lambda: (False, "absent"))

    assert uninstall._run_selected(paths=selected) == 0
    assert skills.list_installed_skills(canonical) == ["alpha", "beta"]


def test_top_level_uninstall_explicitly_requests_global_skill_removal(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(uninstall, "_run_global", lambda: calls.append(True) or 0)

    outcome, _detail = top_level_uninstall._teardown_codex()

    assert outcome == "done"
    assert calls == [True]


def test_doctor_and_uninstall_reject_invalid_mode_without_writes(
    monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "automatic")
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "tokenpak-home"))

    assert doctor.run() == 2
    assert uninstall.run() == 2
    assert "expected shared|workspace|isolated" in capsys.readouterr().err
    assert not (tmp_path / "tokenpak-home").exists()


def test_isolated_doctor_and_uninstall_require_selected_codex_home(
    monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "isolated")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "tokenpak-home"))

    assert doctor.run() == 2
    assert uninstall.run() == 2
    error = capsys.readouterr().err
    assert "isolated mode has no selected home" in error
    assert not (tmp_path / "tokenpak-home").exists()


def test_isolated_inspection_rejects_codex_home_outside_session_root(monkeypatch, tmp_path: Path):
    tokenpak_home = tmp_path / "tokenpak-home"
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "isolated")
    monkeypatch.setenv("TOKENPAK_HOME", str(tokenpak_home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "outside-session"))

    assert doctor.run() == 2
    assert uninstall.run() == 2


def test_uninstall_refuses_live_selected_home_before_any_mutation(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    paths = select_paths(
        "workspace",
        workspace_dir=tmp_path,
        selected_home=tmp_path / "selected-codex",
        source_home=source,
    )
    lease = session_home.SessionLease.acquire(paths, session_id="active-uninstall-test")
    calls: list[str] = []
    monkeypatch.setattr(
        uninstall,
        "remove_mcp",
        lambda *_a, **_k: calls.append("mcp"),
    )
    try:
        assert uninstall._run_selected(paths=paths) == 1
        assert calls == []
        assert paths.pid_sentinel.exists()
    finally:
        lease.release()


def test_uninstall_reprobes_after_lease_before_mutation(monkeypatch, tmp_path: Path):
    paths = select_paths(
        "workspace",
        workspace_dir=tmp_path,
        selected_home=tmp_path / "selected-codex",
        source_home=tmp_path / "source",
    )
    paths.home.mkdir()
    clear = state_lock.LockStatus(
        home=paths.home,
        db_path=paths.home / "state_5.sqlite",
        exists=True,
        locked=False,
    )
    contended = state_lock.LockStatus(
        home=paths.home,
        db_path=paths.home / "state_5.sqlite",
        exists=True,
        locked=True,
        holder_pids=[4242],
        running_pids=[4242],
        detail="state_5.sqlite has live Codex database holder(s): PID 4242 (running)",
    )
    statuses = iter((clear, contended))
    monkeypatch.setattr(state_lock, "probe", lambda _home: next(statuses))
    calls: list[str] = []
    monkeypatch.setattr(uninstall, "_remove_mcp", lambda *_a, **_k: calls.append("mcp"))

    assert uninstall._run_selected(paths=paths) == 1
    assert calls == []
    assert not paths.pid_sentinel.exists()
