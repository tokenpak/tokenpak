"""Exercise the required license workflow against real commit comparisons."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/license-check.yml"


def workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=TokenPak",
            "-c",
            "user.email=hello@tokenpak.ai",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=repo,
        text=True,
    ).strip()


@pytest.fixture
def commits(tmp_path: Path) -> tuple[Path, str, str]:
    git(tmp_path, "init", "-q")
    (tmp_path / "LICENSE").write_text("Apache License\nVersion 2.0\n")
    git(tmp_path, "add", "LICENSE")
    git(tmp_path, "commit", "-qm", "Initial license")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "LICENSE").write_text("MIT License\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/fixture.md").write_text("Test-only fixture\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Change the license")
    return tmp_path, base, git(tmp_path, "rev-parse", "HEAD")


def compare(repo: Path, event: str, base: str, head: str) -> subprocess.CompletedProcess:
    steps = workflow()["jobs"]["license-policy"]["steps"]
    step = next(s for s in steps if s.get("name") == "Compute changed files")
    script = step["run"]
    values = {
        "github.event_name": event,
        "github.event.pull_request.base.sha": base if event == "pull_request" else "",
        "github.event.pull_request.head.sha": head if event == "pull_request" else "",
        "github.event.before": base if event == "push" else "",
        "github.event.after": head if event == "push" else "",
    }
    for expression, value in values.items():
        script = script.replace("${{ " + expression + " }}", value)
    assert "${{" not in script
    return subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_required_context_runs_for_main_pushes_and_pull_requests():
    config = workflow()
    assert config["on"]["push"]["branches"] == ["main"]
    assert config["on"]["pull_request"]["branches"] == ["main"]
    assert config["jobs"]["license-policy"]["name"] == (
        "No superseded first-party license declarations"
    )


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_event_delta_reaches_the_real_license_scanner(commits, event):
    repo, base, head = commits
    result = compare(repo, event, base, head)
    assert result.returncode == 0, result.stderr
    assert (repo / "changed-files.txt").read_text().splitlines() == ["LICENSE"]
    scan = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release_gate/license_policy_scan.py"),
            "--root",
            str(repo),
            "--ledger",
            str(ROOT / "decisions/ledger/license.md"),
            "--paths-from",
            str(repo / "changed-files.txt"),
            "--annotation",
            "none",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert scan.returncode == 1, scan.stdout + scan.stderr


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("invalid_base", ["", "0" * 40, "1" * 40])
def test_unusable_comparison_cannot_report_a_successful_empty_scan(
    commits,
    event,
    invalid_base,
):
    repo, _, head = commits
    result = compare(repo, event, invalid_base, head)
    assert result.returncode != 0
    assert not (repo / "changed-files.txt").exists()


def test_only_excluded_test_changes_are_a_valid_empty_delta(commits):
    repo, _, base = commits
    (repo / "tests/fixture.md").write_text("Updated test-only fixture\n")
    git(repo, "add", "tests/fixture.md")
    git(repo, "commit", "-qm", "Update fixture")
    result = compare(repo, "push", base, git(repo, "rev-parse", "HEAD"))
    assert result.returncode == 0, result.stderr
    assert (repo / "changed-files.txt").read_text() == ""
