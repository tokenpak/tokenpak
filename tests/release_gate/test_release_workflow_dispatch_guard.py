"""Std 12 §3.3 release-dispatch guard regressions.

Confirms that the ``build``, ``release``, and ``publish`` jobs in
``.github/workflows/release.yml`` require a real tag-PUSH event
(``github.event_name == 'push'``) — not merely a tag ref. A ``workflow_dispatch``
against a v-tag must NOT be able to (re)build, (re)create a GitHub Release, or
publish, which would otherwise spoof/overwrite a release for an already-promoted
tag with no tag-push event and no second human gate.

Also confirms the workflow-steps snapshot pins those guards and the post-release
site notification. The site fixture executes the real shell step with a fake
``gh`` binary, proving the present-token, absent-token, and failed-release paths
without making a network request or using a credential.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_SNAPSHOT = _REPO_ROOT / "tokenpak" / "_snapshots" / "workflow-steps.json"

_PUSH_TERM = "github.event_name == 'push'"
_TAG_TERM = "startsWith(github.ref, 'refs/tags/v')"
_GUARDED_JOBS = ("build", "release", "publish")
_SITE_SYNC_JOB = "site-sync"
_SITE_SYNC_STEP = "Dispatch release-synced event to site"


def _release_jobs() -> dict:
    data = yaml.safe_load(_RELEASE_YML.read_text(encoding="utf-8"))
    return data["jobs"]


def _site_sync_job_and_step() -> tuple[dict, dict]:
    job = _release_jobs()[_SITE_SYNC_JOB]
    steps = {step.get("name"): step for step in job.get("steps", [])}
    return job, steps[_SITE_SYNC_STEP]


def _run_site_sync_step(
    tmp_path: Path, *, token: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Execute the workflow shell with an inert fake ``gh`` executable."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "gh-calls.bin"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "{\n"
        "  printf 'CALL\\0'\n"
        "  printf '%s\\0' \"$@\"\n"
        '} >> "$GH_CALL_LOG"\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    _, step = _site_sync_job_and_step()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "GH_TOKEN": token,
            "GH_CALL_LOG": str(call_log),
            "RELEASE_TAG": "v9.9.9-fixture",
            "RELEASE_SHA": "0" * 40,
        }
    )
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, call_log


def test_release_yml_parses_and_has_guarded_jobs():
    jobs = _release_jobs()
    for job in _GUARDED_JOBS:
        assert job in jobs, f"release.yml missing job: {job}"


def test_build_release_publish_require_tag_push_event():
    """Mutate-to-red: a ref-only ``if`` (no ``event_name == 'push'``) is exactly
    the Std 12 §3.3 defect and must fail this assertion."""
    jobs = _release_jobs()
    for job in _GUARDED_JOBS:
        cond = jobs[job].get("if", "")
        assert _PUSH_TERM in cond, (
            f"job '{job}' is not gated on a real tag-push event (missing `{_PUSH_TERM}`): {cond!r}"
        )
        assert _TAG_TERM in cond, f"job '{job}' lost its tag-ref guard: {cond!r}"


def test_publish_still_excludes_prereleases():
    """Guard parity must not weaken publish's existing rc/alpha/beta exclusion."""
    cond = _release_jobs()["publish"].get("if", "")
    for marker in ("rc", "alpha", "beta"):
        assert f"!contains(github.ref, '{marker}')" in cond, (
            f"publish job lost its pre-release exclusion for {marker!r}: {cond!r}"
        )


def test_branch_dispatch_comment_names_only_the_safe_preflight_jobs():
    workflow = _RELEASE_YML.read_text(encoding="utf-8")
    assert "runs the test, chaos, release-gate snapshot, and leak-scan jobs" in workflow
    assert "green branch" in workflow
    assert "not evidence for those surfaces" in workflow


def test_site_sync_requires_successful_release_without_a_bypass():
    """A failed or skipped ``release`` uses default dependency skip semantics."""
    job, _ = _site_sync_job_and_step()
    assert job.get("needs") == "release"
    condition = str(job.get("if", ""))
    assert "always()" not in condition
    assert not condition.strip()


def test_site_sync_step_binds_secret_endpoint_event_and_payload():
    _, step = _site_sync_job_and_step()
    assert step.get("env") == {
        "GH_TOKEN": "${{ secrets.SITE_REPO_TOKEN }}",
        "RELEASE_TAG": "${{ github.ref_name }}",
        "RELEASE_SHA": "${{ github.sha }}",
    }

    command = step.get("run", "")
    assert 'if [ -z "${GH_TOKEN:-}" ]' in command
    assert "::warning::SITE_REPO_TOKEN is not configured" in command
    assert "exit 0" in command
    assert "repos/tokenpak/site/dispatches" in command
    assert "event_type=release-synced" in command
    assert "client_payload[tag_name]=${RELEASE_TAG}" in command
    assert "client_payload[release_sha]=${RELEASE_SHA}" in command


def test_site_sync_with_secret_calls_fake_gh_once_with_exact_payload(tmp_path):
    result, call_log = _run_site_sync_step(tmp_path, token="<fixture-present>")
    assert result.returncode == 0, result.stderr

    fields = [field.decode() for field in call_log.read_bytes().split(b"\0") if field]
    assert fields.count("CALL") == 1
    args = fields[1:]
    assert args[:3] == ["api", "--method", "POST"]
    assert "repos/tokenpak/site/dispatches" in args
    assert "event_type=release-synced" in args
    assert "client_payload[tag_name]=v9.9.9-fixture" in args
    assert f"client_payload[release_sha]={'0' * 40}" in args


def test_site_sync_without_secret_warns_and_never_calls_fake_gh(tmp_path):
    result, call_log = _run_site_sync_step(tmp_path, token="")
    assert result.returncode == 0, result.stderr
    assert "::warning::SITE_REPO_TOKEN is not configured" in result.stdout
    assert not call_log.exists()


def test_workflow_steps_snapshot_pins_dispatch_guards():
    """The committed snapshot must record each guarded job's ``if`` with the
    push-event term, so a regenerated snapshot after a ref-only regression
    drifts and `make workflow-steps-check` fails."""
    snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    guards = {(g["workflow"], g["job"]): g["if"] for g in snap.get("job_guards", [])}
    for job in _GUARDED_JOBS:
        key = ("release.yml", job)
        assert key in guards, f"workflow-steps snapshot does not pin guard for {key}"
        assert _PUSH_TERM in guards[key], (
            f"snapshot guard for {key} is not push-event gated: {guards[key]!r}"
        )


def test_workflow_steps_snapshot_pins_site_sync_dispatch():
    snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    steps = {(step["workflow"], step["job"], step["name"]) for step in snap.get("steps", [])}
    assert ("release.yml", _SITE_SYNC_JOB, _SITE_SYNC_STEP) in steps
