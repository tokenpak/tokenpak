"""Workflow-step ratchet uniqueness regressions."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT = _REPO_ROOT / "tokenpak" / "_snapshots" / "workflow-steps.json"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


def test_each_guarded_workflow_is_traversed_once():
    """Overlapping guarded globs must not traverse one workflow twice."""
    snapshot = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    recorded_counts: dict[str, int] = {}
    for row in snapshot["steps"]:
        workflow = row["workflow"]
        recorded_counts[workflow] = recorded_counts.get(workflow, 0) + 1

    for workflow_name, recorded_count in recorded_counts.items():
        workflow = yaml.safe_load((_WORKFLOWS / workflow_name).read_text(encoding="utf-8"))
        expected_count = sum(
            len(job.get("steps", []))
            for job in workflow.get("jobs", {}).values()
            if isinstance(job, dict)
        )
        assert recorded_count == expected_count, (
            f"{workflow_name} recorded {recorded_count} steps, expected {expected_count}"
        )
