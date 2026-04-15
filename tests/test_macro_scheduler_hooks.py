"""Tests for macro scheduler, script hooks, and premade macros (M.2-M.4)."""


import pytest
pytest.importorskip("tokenpak._internal.macros.premade_macros", reason="module not available in current build")
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak._internal.macros.scheduler import MacroScheduler, ScheduledMacro
from tokenpak._internal.macros.script_hooks import (
    fire_hook,
    fire_on_budget_alert,
    fire_on_error,
    fire_on_request,
    fire_on_response,
    hook_exists,
    install_hook,
    list_hooks,
    HOOK_NAMES,
)
from tokenpak._internal.macros.premade_macros import (
    PREMADE_MACROS,
    PremadeMacroRunner,
    format_macro_output,
    install_macro,
    list_macros,
    run_macro,
)


# ── MacroScheduler ─────────────────────────────────────────────────────────────

class TestMacroScheduler:
    @pytest.fixture
    def scheduler(self, tmp_path):
        return MacroScheduler(schedule_path=tmp_path / "scheduled.json")

    def test_schedule_cron_creates_record(self, scheduler):
        with patch.object(scheduler, "_add_cron_entry", return_value=True):
            s = scheduler.schedule_cron("morning-standup", "0 9 * * 1-5")
        assert s.name == "morning-standup"
        assert s.schedule_type == "cron"
        assert s.schedule == "0 9 * * 1-5"
        assert s.id is not None
        assert len(s.id) == 8

    def test_schedule_cron_default_command(self, scheduler):
        with patch.object(scheduler, "_add_cron_entry", return_value=True):
            s = scheduler.schedule_cron("my-macro", "*/30 * * * *")
        assert "tokenpak macro run my-macro" in s.command

    def test_schedule_cron_custom_command(self, scheduler):
        with patch.object(scheduler, "_add_cron_entry", return_value=True):
            s = scheduler.schedule_cron("my-macro", "0 8 * * *", command="echo hello")
        assert s.command == "echo hello"

    def test_schedule_at_creates_record(self, scheduler):
        with patch.object(scheduler, "_schedule_at_command", return_value=True):
            s = scheduler.schedule_at("weekly-report", "2026-03-06 09:00")
        assert s.name == "weekly-report"
        assert s.schedule_type == "at"
        assert s.schedule == "2026-03-06 09:00"

    def test_list_scheduled_returns_enabled_only(self, scheduler):
        with patch.object(scheduler, "_add_cron_entry", return_value=True):
            s1 = scheduler.schedule_cron("a", "0 9 * * *")
            s2 = scheduler.schedule_cron("b", "0 10 * * *")
        # Cancel one
        scheduler.cancel(s1.id)
        active = scheduler.list_scheduled()
        ids = [s.id for s in active]
        assert s1.id not in ids
        assert s2.id in ids

    def test_cancel_cron_removes_entry(self, scheduler):
        with patch.object(scheduler, "_add_cron_entry", return_value=True):
            s = scheduler.schedule_cron("x", "0 0 * * *")
        with patch.object(scheduler, "_remove_cron_entry", return_value=True) as mock_rm:
            result = scheduler.cancel(s.id)
        assert result is True
        mock_rm.assert_called_once_with(s.id)

    def test_cancel_unknown_id_returns_false(self, scheduler):
        result = scheduler.cancel("nonexistent")
        assert result is False

    def test_persistence(self, tmp_path):
        """Schedules persist across MacroScheduler instances."""
        path = tmp_path / "scheduled.json"
        s1 = MacroScheduler(schedule_path=path)
        with patch.object(s1, "_add_cron_entry", return_value=True):
            s1.schedule_cron("persist-me", "0 6 * * *")
        s2 = MacroScheduler(schedule_path=path)
        names = [s.name for s in s2.list_scheduled()]
        assert "persist-me" in names


# ── Script Hooks ───────────────────────────────────────────────────────────────

class TestScriptHooks:
    @pytest.fixture
    def hooks_dir(self, tmp_path, monkeypatch):
        """Patch the hooks directory to a temp dir."""
        import tokenpak._internal.macros.script_hooks as sh
        monkeypatch.setattr(sh, "DEFAULT_HOOKS_DIR", tmp_path)
        return tmp_path

    def test_hook_names_defined(self):
        assert "on_request" in HOOK_NAMES
        assert "on_response" in HOOK_NAMES
        assert "on_error" in HOOK_NAMES
        assert "on_budget_alert" in HOOK_NAMES

    def test_hook_does_not_exist_initially(self, hooks_dir):
        assert not hook_exists("on_request")

    def test_install_hook_creates_executable(self, hooks_dir):
        path = install_hook("on_request")
        assert path.exists()
        assert os.access(path, os.X_OK)

    def test_install_hook_unknown_raises(self, hooks_dir):
        with pytest.raises(ValueError, match="Unknown hook"):
            install_hook("on_nonexistent")

    def test_install_hook_custom_content(self, hooks_dir):
        content = "#!/bin/bash\necho 'custom'\n"
        path = install_hook("on_response", script_content=content)
        assert path.read_text() == content

    def test_list_hooks_shows_all_hooks(self, hooks_dir):
        hooks = list_hooks()
        assert set(hooks.keys()) == set(HOOK_NAMES.keys())
        for name, info in hooks.items():
            assert "path" in info
            assert "exists" in info
            assert "executable" in info

    def test_fire_hook_returns_none_if_not_installed(self, hooks_dir):
        result = fire_hook("on_request", {"model": "gpt-4"})
        assert result is None

    def test_fire_hook_executes_and_captures_output(self, hooks_dir):
        script = "#!/bin/bash\ncat  # echo stdin back\n"
        install_hook("on_request", script_content=script)
        result = fire_hook("on_request", {"model": "gpt-4", "provider": "openai"})
        assert result is not None
        assert result["success"] is True
        # stdout should contain the JSON context
        data = json.loads(result["stdout"])
        assert data["model"] == "gpt-4"
        assert "timestamp" in data

    def test_fire_on_request_context(self, hooks_dir):
        received = {}
        def fake_fire(hook_name, context, timeout=30):
            received.update(context)
            return {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        import tokenpak._internal.macros.script_hooks as sh
        orig = sh.fire_hook
        sh.fire_hook = fake_fire
        try:
            fire_on_request("claude-3", "anthropic", 5)
        finally:
            sh.fire_hook = orig
        assert received["model"] == "claude-3"
        assert received["provider"] == "anthropic"
        assert received["messages_count"] == 5

    def test_fire_on_response_context(self, hooks_dir):
        received = {}
        import tokenpak._internal.macros.script_hooks as sh
        orig = sh.fire_hook
        def fake_fire(hook_name, context, timeout=30):
            received.update(context)
            return {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        sh.fire_hook = fake_fire
        try:
            fire_on_response("gpt-4", "openai", 1500, 0.045, 823)
        finally:
            sh.fire_hook = orig
        assert received["tokens_used"] == 1500
        assert received["cost_usd"] == pytest.approx(0.045)
        assert received["latency_ms"] == 823

    def test_fire_on_budget_alert_pct(self, hooks_dir):
        received = {}
        import tokenpak._internal.macros.script_hooks as sh
        orig = sh.fire_hook
        def fake_fire(hook_name, context, timeout=30):
            received.update(context)
            return {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        sh.fire_hook = fake_fire
        try:
            fire_on_budget_alert("daily", 10.0, 8.5)
        finally:
            sh.fire_hook = orig
        assert received["pct_used"] == pytest.approx(85.0)


# ── Premade Macros ─────────────────────────────────────────────────────────────

class TestPremadeMacros:
    def test_premade_macros_defined(self):
        assert "morning-standup" in PREMADE_MACROS
        assert "pre-deploy" in PREMADE_MACROS
        assert "weekly-report" in PREMADE_MACROS

    def test_list_macros(self):
        macros = list_macros()
        names = [m["name"] for m in macros]
        assert "morning-standup" in names
        assert "pre-deploy" in names
        assert "weekly-report" in names

    def test_install_macro(self, tmp_path, monkeypatch):
        import tokenpak._internal.macros.premade_macros as pm
        monkeypatch.setattr(pm, "INSTALL_DIR", tmp_path)
        path = install_macro("morning-standup")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["name"] == "morning-standup"
        assert "steps" in data

    def test_install_unknown_macro_raises(self):
        with pytest.raises(ValueError, match="Unknown premade macro"):
            install_macro("nonexistent-macro")

    def test_run_macro_structure(self):
        runner = PremadeMacroRunner()
        with patch.object(runner, "_run_step") as mock_step:
            mock_step.return_value = {
                "name": "cost_summary",
                "label": "💰 Today's Cost",
                "cmd": "tokenpak status --json",
                "output": '{"cost": 0.42}',
                "error": "",
                "success": True,
                "returncode": 0,
            }
            result = runner.run("morning-standup")
        assert result["name"] == "morning-standup"
        assert "steps" in result
        assert "started_at" in result
        assert "finished_at" in result
        assert result["duration_seconds"] >= 0

    def test_format_output_contains_macro_name(self):
        result = {
            "name": "morning-standup",
            "description": "Test",
            "started_at": "2026-03-05T09:00:00",
            "finished_at": "2026-03-05T09:00:01",
            "duration_seconds": 1.0,
            "steps": [
                {"name": "a", "label": "Test step", "output": "ok", "error": "", "success": True}
            ],
        }
        output = format_macro_output(result)
        assert "MORNING" in output or "morning" in output.lower()
        assert "Test step" in output

    def test_run_step_timeout_handled(self):
        runner = PremadeMacroRunner()
        step = {"name": "slow", "label": "Slow step", "cmd": "sleep 999"}
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 60)):
            result = runner._run_step(step)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()

    def test_weekly_report_has_7day_step(self):
        macro = PREMADE_MACROS["weekly-report"]
        cmds = [s["cmd"] for s in macro["steps"]]
        assert any("7" in c for c in cmds)

    def test_pre_deploy_has_budget_check(self):
        macro = PREMADE_MACROS["pre-deploy"]
        cmds = [s["cmd"] for s in macro["steps"]]
        assert any("budget" in c for c in cmds)
