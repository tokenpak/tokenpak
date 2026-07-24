"""Std 12 §3.3 release-dispatch guard regressions.

Confirms that the ``build``, ``release``, and ``publish`` jobs in
``.github/workflows/release.yml`` require a real tag-PUSH event
(``github.event_name == 'push'``) — not merely a tag ref. A ``workflow_dispatch``
against a v-tag must NOT be able to (re)build, (re)create a GitHub Release, or
publish, which would otherwise spoof/overwrite a release for an already-promoted
tag with no tag-push event and no second human gate.

Also confirms the workflow-steps snapshot pins those guards, so a ref-only
regression trips ``make workflow-steps-check`` (the static CI assertion required
by the packet acceptance).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_SNAPSHOT = _REPO_ROOT / "tokenpak" / "_snapshots" / "workflow-steps.json"

_PUSH_TERM = "github.event_name == 'push'"
_TAG_TERM = "startsWith(github.ref, 'refs/tags/v')"
_GUARDED_JOBS = ("build", "release", "publish")


def _release_jobs() -> dict:
    data = yaml.safe_load(_RELEASE_YML.read_text(encoding="utf-8"))
    return data["jobs"]


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
