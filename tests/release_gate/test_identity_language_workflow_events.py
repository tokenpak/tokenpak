"""Event-routing regressions for the identity-language workflow."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "identity-language-check.yml"
_CONTEXT_NAME = "No internal identity / workflow language in changed files"


def _workflow() -> dict:
    # BaseLoader preserves GitHub's literal ``on`` key instead of applying
    # YAML 1.1's legacy boolean coercion.
    data = yaml.load(_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(data, dict)
    return data


def _compute_changed_files_script() -> str:
    steps = _workflow()["jobs"]["identity-language"]["steps"]
    matches = [step for step in steps if step.get("name") == "Compute changed files"]
    assert len(matches) == 1
    return matches[0]["run"]


def test_workflow_runs_for_pull_requests_and_main_pushes():
    triggers = _workflow()["on"]
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["push"]["branches"] == ["main"]


def test_required_context_name_is_unchanged():
    job = _workflow()["jobs"]["identity-language"]
    assert job["name"] == _CONTEXT_NAME


def test_each_event_uses_its_own_sha_pair_and_fails_closed():
    script = _compute_changed_files_script()
    assignments = re.findall(r'^\s*(base|head)="\$\{\{\s*([^}]+?)\s*\}\}"$', script, re.MULTILINE)
    assert assignments == [
        ("base", "github.event.pull_request.base.sha"),
        ("head", "github.event.pull_request.head.sha"),
        ("base", "github.event.before"),
        ("head", "github.event.after"),
    ]

    zero_base_guard = 'if [[ -z "$base" || "$base" =~ ^0+$ ]]; then'
    zero_head_guard = 'if [[ -z "$head" || "$head" =~ ^0+$ ]]; then'
    assert zero_base_guard in script
    assert zero_head_guard in script
    assert script.index(zero_base_guard) < script.index("git diff")
    assert script.index(zero_head_guard) < script.index("git diff")
    assert re.search(r"\*\)\s+echo .*Unsupported event.*\s+exit 1\s+;;", script)

    # Comparison failure must stop the step. Only the later filtering pipeline
    # may tolerate grep's ordinary no-match status.
    diff_line = next(line for line in script.splitlines() if "git diff" in line)
    assert "|| true" not in diff_line
    assert "> all-changed-files.txt" in diff_line
    assert script.index("git diff") < script.index("grep -vE")

    # SHA selection has no ref-based fallback that could turn a missing event
    # payload into an apparently successful scan of the wrong comparison.
    assert "github.sha" not in script
    assert "HEAD^" not in script
