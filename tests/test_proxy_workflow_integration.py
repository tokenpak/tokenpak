"""tests/test_proxy_workflow_integration.py

Integration tests for ProxyWorkflowAdapter (proxy_workflow.py).

AC coverage:
  AC1 — TOKENPAK_WORKFLOW_TRACKING=0 (default) → no behavior change, zero perf impact
  AC2 — TOKENPAK_WORKFLOW_TRACKING=1 → each proxy request gets a workflow record
  AC3 — Proxy crash mid-request → workflow shows RUNNING step → recover surfaces it
  AC4 — tokenpak workflow list --type proxy works
  AC5 — All new tests pass, no existing tests broken
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — reload proxy_workflow with a given env var state
# ---------------------------------------------------------------------------

def _reload_proxy_workflow(tracking: str, workflow_dir: str):
    """Reload proxy_workflow module with custom env + workflow dir, return module."""
    env_patch = {"TOKENPAK_WORKFLOW_TRACKING": tracking}

    # Clear module cache so WORKFLOW_TRACKING_ENABLED is re-evaluated
    for mod_name in list(sys.modules.keys()):
        if "proxy_workflow" in mod_name or (
            "tokenpak.agentic" in mod_name and "workflow" in mod_name
        ):
            del sys.modules[mod_name]

    with patch.dict(os.environ, env_patch, clear=False):
        # Point WorkflowManager to the temp dir
        with patch(
            "tokenpak.agentic.workflow.DEFAULT_WORKFLOW_DIR",
            Path(workflow_dir),
        ):
            import tokenpak.agentic.proxy_workflow as pw
            # Override the manager so it uses our temp dir
            pw._manager_override = None

            def _patched_get_manager():
                if not pw.WORKFLOW_TRACKING_ENABLED:
                    return None
                from tokenpak.agentic.workflow import WorkflowManager
                return WorkflowManager(workflow_dir=workflow_dir)

            pw._get_manager = _patched_get_manager
            return pw


# ---------------------------------------------------------------------------
# AC1 — Feature flag OFF → zero side effects
# ---------------------------------------------------------------------------

class TestFeatureFlagOff:
    """TOKENPAK_WORKFLOW_TRACKING unset / 0 → all calls are no-ops."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pw = _reload_proxy_workflow("0", self.tmpdir)

    def test_tracking_disabled(self):
        assert self.pw.WORKFLOW_TRACKING_ENABLED is False

    def test_start_returns_none(self):
        result = self.pw.start_proxy_workflow("req-001")
        assert result is None

    def test_advance_step_noop(self):
        # Should not raise even with None wf_id
        self.pw.advance_step(None, "vault_inject", "compress")

    def test_complete_workflow_noop(self):
        self.pw.complete_workflow(None)

    def test_fail_step_noop(self):
        self.pw.fail_step(None, "forward", error="boom")

    def test_recover_returns_empty(self):
        result = self.pw.recover_proxy_workflows()
        assert result == []

    def test_no_files_written(self):
        """No workflow files should be created when tracking is off."""
        self.pw.start_proxy_workflow("req-no-op")
        files = list(Path(self.tmpdir).glob("*.json"))
        assert files == [], "No files should be written when tracking is disabled"


# ---------------------------------------------------------------------------
# AC2 — Feature flag ON → workflow created per request
# ---------------------------------------------------------------------------

class TestFeatureFlagOn:
    """TOKENPAK_WORKFLOW_TRACKING=1 → a workflow record is persisted per request."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pw = _reload_proxy_workflow("1", self.tmpdir)

    def test_tracking_enabled(self):
        assert self.pw.WORKFLOW_TRACKING_ENABLED is True

    def test_start_returns_request_id(self):
        wf_id = self.pw.start_proxy_workflow("req-abc")
        assert wf_id == "req-abc"

    def test_workflow_file_created(self):
        self.pw.start_proxy_workflow("req-file-check")
        files = list(Path(self.tmpdir).glob("*.json"))
        assert len(files) == 1

    def test_workflow_has_proxy_tag(self):
        import json
        self.pw.start_proxy_workflow("req-tag-check")
        wf_file = next(Path(self.tmpdir).glob("*.json"))
        data = json.loads(wf_file.read_text())
        assert "proxy" in data["tags"]

    def test_full_happy_path(self):
        """Complete all steps successfully → status COMPLETED."""
        import json
        wf_id = self.pw.start_proxy_workflow("req-happy")
        assert wf_id is not None

        # vault_inject done → begin compress
        self.pw.advance_step(wf_id, "vault_inject", "compress")
        # compress done → begin forward
        self.pw.advance_step(wf_id, "compress", "forward")
        # forward done → begin log_metrics
        self.pw.advance_step(wf_id, "forward", "log_metrics")
        # finalize
        self.pw.complete_workflow(wf_id)

        wf_file = Path(self.tmpdir) / f"{wf_id}.json"
        data = json.loads(wf_file.read_text())
        assert data["status"] == "completed"
        done_steps = [s for s in data["steps"] if s["status"] == "completed"]
        assert len(done_steps) == 4  # all 4 steps completed

    def test_metadata_stored(self):
        import json
        self.pw.start_proxy_workflow("req-meta", metadata={"path": "/v1/messages", "method": "POST"})
        wf_file = Path(self.tmpdir) / "req-meta.json"
        data = json.loads(wf_file.read_text())
        assert data["metadata"]["path"] == "/v1/messages"

    def test_multiple_requests_independent(self):
        self.pw.start_proxy_workflow("req-A")
        self.pw.start_proxy_workflow("req-B")
        files = list(Path(self.tmpdir).glob("*.json"))
        assert len(files) == 2


# ---------------------------------------------------------------------------
# AC3 — Crash mid-request → RUNNING step visible → recover surfaces it
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    """Simulates a proxy crash mid-request."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pw = _reload_proxy_workflow("1", self.tmpdir)

    def test_failed_step_does_not_crash_caller(self):
        """fail_step must never raise — the proxy response must continue."""
        wf_id = self.pw.start_proxy_workflow("req-crash")
        self.pw.advance_step(wf_id, "vault_inject", "compress")
        self.pw.advance_step(wf_id, "compress", "forward")
        # Simulate crash during forward
        self.pw.fail_step(wf_id, "forward", error="ConnectionRefusedError: upstream unreachable")
        # No exception = pass

    def test_failed_step_marks_workflow_failed(self):
        import json
        wf_id = self.pw.start_proxy_workflow("req-fail-state")
        self.pw.advance_step(wf_id, "vault_inject", "compress")
        self.pw.fail_step(wf_id, "compress", error="timeout")
        data = json.loads((Path(self.tmpdir) / f"{wf_id}.json").read_text())
        assert data["status"] == "failed"

    def test_incomplete_workflow_surfaced_by_recover(self):
        """A workflow that never completes (simulated crash) appears in recover()."""
        wf_id = self.pw.start_proxy_workflow("req-dangling")
        # Start vault_inject but never advance (crash simulation)
        # recover_proxy_workflows should find it
        results = self.pw.recover_proxy_workflows()
        assert any(wf["id"] == wf_id for wf in results), (
            "Dangling proxy workflow should be returned by recover_proxy_workflows()"
        )

    def test_recover_returns_only_proxy_workflows(self):
        """Non-proxy workflows should not appear in recover_proxy_workflows()."""
        from tokenpak.agentic.workflow import WorkflowManager, WorkflowStep
        mgr = WorkflowManager(workflow_dir=self.tmpdir)
        # Create a non-proxy workflow
        non_proxy = mgr.create(
            name="deploy-something",
            steps=[WorkflowStep("build"), WorkflowStep("deploy", depends_on=["build"])],
            tags=["deploy"],
        )
        mgr.start(non_proxy.id)
        mgr.begin_step(non_proxy.id, "build")

        # Create a proxy workflow (dangling)
        proxy_wf_id = self.pw.start_proxy_workflow("req-only-proxy")

        results = self.pw.recover_proxy_workflows()
        result_ids = {wf["id"] for wf in results}
        assert proxy_wf_id in result_ids
        assert non_proxy.id not in result_ids, "Non-proxy workflows must not appear in proxy recover"

    def test_completed_workflow_not_in_recover(self):
        """A completed workflow should not be surfaced by recover."""
        wf_id = self.pw.start_proxy_workflow("req-done")
        self.pw.advance_step(wf_id, "vault_inject", "compress")
        self.pw.advance_step(wf_id, "compress", "forward")
        self.pw.advance_step(wf_id, "forward", "log_metrics")
        self.pw.complete_workflow(wf_id)

        results = self.pw.recover_proxy_workflows()
        assert not any(wf["id"] == wf_id for wf in results)


# ---------------------------------------------------------------------------
# AC4 — CLI: tokenpak workflow list --type proxy
# ---------------------------------------------------------------------------

class TestWorkflowCLI:
    """CLI-level tests for the workflow command with --type proxy."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def _make_manager(self):
        from tokenpak.agentic.workflow import WorkflowManager
        return WorkflowManager(workflow_dir=self.tmpdir)

    def test_list_type_proxy_filters_correctly(self):
        """--type proxy should only return workflows tagged 'proxy'."""
        from click.testing import CliRunner
        from tokenpak.cli.commands.workflow import list_cmd

        mgr = self._make_manager()

        # Create a proxy-tagged workflow
        from tokenpak.agentic.workflow import WorkflowStep
        proxy_wf = mgr.create(
            name="proxy-request-req-xyz",
            steps=[WorkflowStep("vault_inject"), WorkflowStep("forward", depends_on=["vault_inject"])],
            tags=["proxy", "req-xyz"],
        )
        # Create a non-proxy workflow
        other_wf = mgr.create(
            name="deploy-pipeline",
            steps=[WorkflowStep("build"), WorkflowStep("deploy", depends_on=["build"])],
            tags=["deploy"],
        )

        with patch("tokenpak.agentic.workflow.DEFAULT_WORKFLOW_DIR", Path(self.tmpdir)):
            with patch("tokenpak.cli.commands.workflow.get_manager") as mock_mgr:
                mock_mgr.return_value = mgr
                runner = CliRunner()
                result = runner.invoke(list_cmd, ["--type", "proxy"])

        assert result.exit_code == 0
        assert "proxy-request-req-xyz" in result.output
        assert "deploy-pipeline" not in result.output

    def test_recover_type_proxy_filters_correctly(self):
        """tokenpak workflow recover --type proxy only shows incomplete proxy workflows."""
        from click.testing import CliRunner
        from tokenpak.cli.commands.workflow import recover_cmd
        from tokenpak.agentic.workflow import WorkflowManager, WorkflowStep

        mgr = self._make_manager()

        # Create incomplete proxy workflow
        proxy_wf = mgr.create(
            name="proxy-request-dangling",
            steps=[WorkflowStep("vault_inject"), WorkflowStep("forward", depends_on=["vault_inject"])],
            tags=["proxy"],
        )
        mgr.start(proxy_wf.id)
        mgr.begin_step(proxy_wf.id, "vault_inject")

        # Create incomplete non-proxy workflow
        other_wf = mgr.create(
            name="non-proxy-pipeline",
            steps=[WorkflowStep("build"), WorkflowStep("deploy", depends_on=["build"])],
            tags=["ci"],
        )
        mgr.start(other_wf.id)

        with patch("tokenpak.cli.commands.workflow.get_manager", return_value=mgr):
            runner = CliRunner()
            result = runner.invoke(recover_cmd, ["--type", "proxy"])

        assert result.exit_code == 0
        assert "proxy-request-dangling" in result.output
        assert "non-proxy-pipeline" not in result.output

    def test_list_no_type_shows_all(self):
        """Without --type, all workflows are shown."""
        from click.testing import CliRunner
        from tokenpak.cli.commands.workflow import list_cmd
        from tokenpak.agentic.workflow import WorkflowManager, WorkflowStep

        mgr = self._make_manager()
        mgr.create(
            name="proxy-request-A",
            steps=[WorkflowStep("vault_inject")],
            tags=["proxy"],
        )
        mgr.create(
            name="deploy-thing",
            steps=[WorkflowStep("build")],
            tags=["deploy"],
        )

        with patch("tokenpak.cli.commands.workflow.get_manager", return_value=mgr):
            runner = CliRunner()
            result = runner.invoke(list_cmd, [])

        assert result.exit_code == 0
        assert "proxy-request-A" in result.output
        assert "deploy-thing" in result.output


# ---------------------------------------------------------------------------
# AC5 — Regression: proxy_workflow import doesn't break when tracking is off
# ---------------------------------------------------------------------------

class TestRegressionNoBreakage:
    """Ensure importing proxy_workflow with flag OFF has zero side effects."""

    def test_import_with_flag_off_is_safe(self):
        with patch.dict(os.environ, {"TOKENPAK_WORKFLOW_TRACKING": "0"}, clear=False):
            for mod in list(sys.modules.keys()):
                if "proxy_workflow" in mod:
                    del sys.modules[mod]
            import tokenpak.agentic.proxy_workflow as pw
            assert pw.WORKFLOW_TRACKING_ENABLED is False
            # All functions should return None / [] without raising
            assert pw.start_proxy_workflow("r1") is None
            assert pw.recover_proxy_workflows() == []
            pw.advance_step(None, "vault_inject")
            pw.complete_workflow(None)
            pw.fail_step(None, "forward", error="test")

    def test_proxy_template_in_workflow_templates(self):
        """The 'proxy' template must be registered in WORKFLOW_TEMPLATES."""
        from tokenpak.agentic.workflow import WORKFLOW_TEMPLATES, list_templates
        assert "proxy" in WORKFLOW_TEMPLATES
        assert "proxy" in list_templates()
        steps = WORKFLOW_TEMPLATES["proxy"]
        step_names = [s["name"] for s in steps]
        assert step_names == ["vault_inject", "compress", "forward", "log_metrics"]

    def test_proxy_template_step_dependencies(self):
        """Steps in proxy template should have correct dependency chain."""
        from tokenpak.agentic.workflow import WORKFLOW_TEMPLATES
        steps = {s["name"]: s for s in WORKFLOW_TEMPLATES["proxy"]}
        assert steps["vault_inject"]["depends_on"] == []
        assert "vault_inject" in steps["compress"]["depends_on"]
        assert "compress" in steps["forward"]["depends_on"]
        assert "forward" in steps["log_metrics"]["depends_on"]
