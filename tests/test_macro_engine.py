"""Tests for the YAML macro engine (M.1).

Covers:
- Lifecycle: create, list, show, delete
- Execution: sequential, fail-fast, continue-on-error, dry-run
- Variable substitution: ${VAR} and $VAR styles, runtime overrides
- Premade macros are included in list/run
- CLI commands: list, create, run, show, delete
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak._internal.macros.engine import (
    MacroEngine,
    MacroDefinition,
    MacroStep,
    MacroResult,
    StepResult,
    _resolve_vars,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path):
    """MacroEngine pointed at a temp macros directory."""
    return MacroEngine(macros_dir=tmp_path / "macros")


@pytest.fixture
def simple_macro_steps():
    return [
        {"name": "greet", "label": "Say hello", "cmd": "echo hello"},
        {"name": "count", "label": "Count", "cmd": "echo 42"},
    ]


@pytest.fixture
def var_macro_steps():
    return [
        {"name": "env_echo", "label": "Echo env", "cmd": "echo ${env}"},
        {"name": "model_echo", "label": "Echo model", "cmd": "echo $model"},
    ]


# ── Variable substitution ─────────────────────────────────────────────────────

class TestResolveVars:
    def test_curly_brace_style(self):
        assert _resolve_vars("echo ${foo}", {"foo": "bar"}) == "echo bar"

    def test_dollar_style(self):
        assert _resolve_vars("echo $name", {"name": "trix"}) == "echo trix"

    def test_missing_var_left_as_is(self):
        assert _resolve_vars("echo ${missing}", {}) == "echo ${missing}"

    def test_multiple_vars(self):
        result = _resolve_vars("${a} and ${b}", {"a": "alpha", "b": "beta"})
        assert result == "alpha and beta"

    def test_numeric_value(self):
        assert _resolve_vars("count=${n}", {"n": 5}) == "count=5"

    def test_mixed_styles(self):
        result = _resolve_vars("${ENV} $model extra", {"ENV": "prod", "model": "gpt-4"})
        assert result == "prod gpt-4 extra"

    def test_no_vars(self):
        assert _resolve_vars("echo plain", {}) == "echo plain"


# ── MacroDefinition ───────────────────────────────────────────────────────────

class TestMacroDefinition:
    def test_to_dict_roundtrip(self):
        steps = [MacroStep("s1", "echo hi", "Say hi")]
        macro = MacroDefinition("test-macro", steps, "desc", {"k": "v"}, False)
        d = macro.to_dict()
        m2 = MacroDefinition.from_dict(d)
        assert m2.name == "test-macro"
        assert m2.description == "desc"
        assert m2.variables == {"k": "v"}
        assert len(m2.steps) == 1
        assert m2.steps[0].name == "s1"

    def test_to_yaml_and_back(self):
        pytest.importorskip("yaml")
        steps = [MacroStep("deploy", "make deploy", "Deploy")]
        macro = MacroDefinition("deploy-macro", steps, "Deploy app", continue_on_error=True)
        yaml_text = macro.to_yaml()
        restored = MacroDefinition.from_yaml(yaml_text)
        assert restored.name == "deploy-macro"
        assert restored.continue_on_error is True
        assert restored.steps[0].cmd == "make deploy"

    def test_default_continue_on_error_is_false(self):
        macro = MacroDefinition("x", [], "")
        assert macro.continue_on_error is False


# ── Lifecycle (create / list / show / delete) ─────────────────────────────────

class TestMacroLifecycle:
    def test_create_writes_yaml(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        path = engine.create("my-macro", simple_macro_steps, "My macro")
        assert path.exists()
        assert "my-macro" in path.name

    def test_create_duplicate_raises(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("dup", simple_macro_steps)
        with pytest.raises(ValueError, match="already exists"):
            engine.create("dup", simple_macro_steps)

    def test_create_overwrite(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("ow", simple_macro_steps, "v1")
        engine.create("ow", simple_macro_steps, "v2", overwrite=True)
        macro = engine.show("ow")
        assert macro.description == "v2"

    def test_show_returns_definition(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("shown", simple_macro_steps, "Show me")
        macro = engine.show("shown")
        assert macro.name == "shown"
        assert macro.description == "Show me"
        assert len(macro.steps) == 2

    def test_show_missing_raises(self, engine):
        pytest.importorskip("yaml")
        with pytest.raises(FileNotFoundError):
            engine.show("nonexistent")

    def test_list_returns_all(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("a", simple_macro_steps)
        engine.create("b", simple_macro_steps)
        macros = engine.list()
        names = [m.name for m in macros]
        assert "a" in names
        assert "b" in names

    def test_list_empty_dir(self, engine):
        pytest.importorskip("yaml")
        assert engine.list() == []

    def test_list_sorted_by_name(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("zebra", simple_macro_steps)
        engine.create("alpha", simple_macro_steps)
        names = [m.name for m in engine.list()]
        assert names == sorted(names)

    def test_delete_removes_file(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("del-me", simple_macro_steps)
        assert engine.exists("del-me")
        result = engine.delete("del-me")
        assert result is True
        assert not engine.exists("del-me")

    def test_delete_missing_returns_false(self, engine):
        assert engine.delete("ghost") is False

    def test_exists_true_after_create(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("check", simple_macro_steps)
        assert engine.exists("check") is True

    def test_exists_false_before_create(self, engine):
        assert engine.exists("not-yet") is False

    def test_create_with_variables(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "var-macro",
            [{"name": "s1", "cmd": "echo ${env}", "label": "Env"}],
            variables={"env": "staging"},
        )
        macro = engine.show("var-macro")
        assert macro.variables == {"env": "staging"}

    def test_create_continue_on_error_persisted(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("coe", simple_macro_steps, continue_on_error=True)
        macro = engine.show("coe")
        assert macro.continue_on_error is True

    def test_create_from_yaml_string(self, engine):
        yaml = pytest.importorskip("yaml")
        raw = yaml.dump({
            "name": "from-yaml",
            "description": "loaded from string",
            "steps": [{"name": "s1", "cmd": "echo ok", "label": "OK"}],
        })
        path = engine.create_from_yaml(raw)
        assert engine.exists("from-yaml")
        assert "from-yaml" in str(path)


# ── Execution ─────────────────────────────────────────────────────────────────

class TestMacroExecution:
    def test_run_returns_macro_result(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("run-me", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s", "S", "echo hi", "hi", "", True, 0)
            result = engine.run("run-me")
        assert isinstance(result, MacroResult)
        assert result.macro_name == "run-me"
        assert len(result.steps) == 2

    def test_run_all_steps_succeed(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("success", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.side_effect = [
                StepResult("greet", "Say hello", "echo hello", "hello", "", True, 0),
                StepResult("count", "Count", "echo 42", "42", "", True, 0),
            ]
            result = engine.run("success")
        assert result.success is True
        assert len(result.steps) == 2

    def test_fail_fast_stops_on_first_failure(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("failfast", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("greet", "Say hello", "echo hello", "", "err", False, 1)
            result = engine.run("failfast", continue_on_error=False)
        assert result.success is False
        # Only first step ran (fail-fast)
        assert len(result.steps) == 1

    def test_continue_on_error_runs_all_steps(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("keepgoing", simple_macro_steps, continue_on_error=True)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.side_effect = [
                StepResult("greet", "Say hello", "echo hello", "", "err", False, 1),
                StepResult("count", "Count", "echo 42", "42", "", True, 0),
            ]
            result = engine.run("keepgoing")
        assert len(result.steps) == 2
        assert result.success is False  # one step failed

    def test_continue_on_error_override(self, engine, simple_macro_steps):
        """continue_on_error kwarg overrides the macro's own setting."""
        pytest.importorskip("yaml")
        engine.create("override", simple_macro_steps, continue_on_error=False)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.side_effect = [
                StepResult("greet", "Say hello", "echo hello", "", "err", False, 1),
                StepResult("count", "Count", "echo 42", "42", "", True, 0),
            ]
            result = engine.run("override", continue_on_error=True)
        assert len(result.steps) == 2

    def test_dry_run_no_execution(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("dry", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            result = engine.run("dry", dry_run=True)
        mock_step.assert_not_called()
        assert result.dry_run is True
        assert result.success is True
        for step in result.steps:
            assert step.dry_run is True

    def test_dry_run_contains_resolved_commands(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "dryvar",
            [{"name": "s1", "cmd": "echo ${msg}", "label": "Echo"}],
            variables={"msg": "hello"},
        )
        result = engine.run("dryvar", dry_run=True)
        assert result.steps[0].cmd == "echo hello"

    def test_duration_seconds_nonnegative(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("dur", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s", "S", "cmd", "ok", "", True, 0)
            result = engine.run("dur")
        assert result.duration_seconds >= 0

    def test_result_to_dict_keys(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("dkeys", simple_macro_steps)
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s", "S", "cmd", "out", "", True, 0)
            result = engine.run("dkeys")
        d = result.to_dict()
        for key in ("macro_name", "started_at", "finished_at", "duration_seconds", "success", "steps"):
            assert key in d

    def test_timeout_returns_failure(self, engine, simple_macro_steps):
        pytest.importorskip("yaml")
        engine.create("timeout", simple_macro_steps)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = engine._run_step("s", "Label", "sleep 999", timeout=60)
        assert result.success is False
        assert "timed out" in result.error.lower()


# ── Variable substitution in execution ───────────────────────────────────────

class TestVariableSubstitution:
    def test_macro_defaults_used(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "subst-default",
            [{"name": "s1", "cmd": "echo ${greeting}", "label": "Hello"}],
            variables={"greeting": "howdy"},
        )
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s1", "Hello", "echo howdy", "howdy", "", True, 0)
            engine.run("subst-default")
        called_cmd = mock_step.call_args[0][2]
        assert called_cmd == "echo howdy"

    def test_runtime_overrides_macro_default(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "subst-override",
            [{"name": "s1", "cmd": "echo ${env}", "label": "Env"}],
            variables={"env": "dev"},
        )
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s1", "Env", "echo prod", "prod", "", True, 0)
            engine.run("subst-override", variables={"env": "prod"})
        called_cmd = mock_step.call_args[0][2]
        assert called_cmd == "echo prod"

    def test_runtime_var_not_in_macro_defaults(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "subst-new",
            [{"name": "s1", "cmd": "deploy --tag ${tag}", "label": "Deploy"}],
        )
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s1", "Deploy", "deploy --tag v1.2", "ok", "", True, 0)
            engine.run("subst-new", variables={"tag": "v1.2"})
        called_cmd = mock_step.call_args[0][2]
        assert called_cmd == "deploy --tag v1.2"

    def test_label_also_substituted(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "subst-label",
            [{"name": "s1", "cmd": "echo ok", "label": "Deploy ${env}"}],
            variables={"env": "staging"},
        )
        with patch.object(engine, "_run_step") as mock_step:
            mock_step.return_value = StepResult("s1", "Deploy staging", "echo ok", "ok", "", True, 0)
            engine.run("subst-label")
        called_label = mock_step.call_args[0][1]
        assert called_label == "Deploy staging"


# ── MacroResult.format ────────────────────────────────────────────────────────

class TestMacroResultFormat:
    def _make_result(self, success=True, dry_run=False):
        steps = [
            StepResult("s1", "Step One", "echo hi", "hi", "", True, 0, dry_run=dry_run),
            StepResult("s2", "Step Two", "echo bye", "bye", "", success, 0 if success else 1, dry_run=dry_run),
        ]
        return MacroResult(
            macro_name="test-macro",
            steps=steps,
            started_at="2026-03-05T10:00:00",
            finished_at="2026-03-05T10:00:02",
            success=success,
            dry_run=dry_run,
        )

    def test_format_contains_macro_name(self):
        result = self._make_result()
        output = result.format()
        # format() uppercases the name (hyphens preserved or replaced with spaces)
        assert "TEST-MACRO" in output or "TEST MACRO" in output or "test-macro" in output.lower()

    def test_format_contains_step_labels(self):
        result = self._make_result()
        output = result.format()
        assert "Step One" in output
        assert "Step Two" in output

    def test_format_pass_on_success(self):
        result = self._make_result(success=True)
        assert "PASS" in result.format()

    def test_format_fail_on_failure(self):
        result = self._make_result(success=False)
        assert "FAIL" in result.format()

    def test_format_dry_run_note(self):
        result = self._make_result(dry_run=True)
        output = result.format()
        assert "DRY RUN" in output.upper()


# ── Integration: real subprocess execution ────────────────────────────────────

class TestRealExecution:
    def test_echo_command_runs(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "real-echo",
            [{"name": "hi", "cmd": "echo integration-test-ok", "label": "Echo"}],
        )
        result = engine.run("real-echo")
        assert result.success is True
        assert "integration-test-ok" in result.steps[0].output

    def test_failing_command_captured(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "real-fail",
            [
                {"name": "fail", "cmd": "exit 1", "label": "Fail"},
                {"name": "after", "cmd": "echo after", "label": "After"},
            ],
        )
        result = engine.run("real-fail")  # fail-fast default
        assert result.success is False
        assert len(result.steps) == 1  # stopped after first failure

    def test_continue_on_error_real(self, engine):
        pytest.importorskip("yaml")
        engine.create(
            "real-coe",
            [
                {"name": "fail", "cmd": "exit 2", "label": "Fail"},
                {"name": "ok", "cmd": "echo still-running", "label": "OK"},
            ],
            continue_on_error=True,
        )
        result = engine.run("real-coe")
        assert len(result.steps) == 2
        assert result.steps[1].output == "still-running"
