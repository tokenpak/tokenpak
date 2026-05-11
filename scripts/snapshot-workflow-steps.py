#!/usr/bin/env python3
"""Generate tokenpak/_snapshots/workflow-steps.json from .github/workflows/*.yml.

This script is the workflow-step ratchet generator. Its output is the canonical
snapshot of every step across every GitHub Actions workflow in the repository.

Per the release-gate trust contract:

    A PR removing a step from any release workflow MUST declare
    `removes-ci-step: <step.id>` (one line per removed step) in the PR body.
    The ratchet check (`make workflow-steps-check`) compares the
    committed snapshot against the regenerated snapshot and fails if a step
    has disappeared without a declaration.

Determinism:
    - Workflow file list is sorted lexically before iteration.
    - Within a workflow, jobs are processed in insertion order (Python 3.7+
      preserves dict order), then steps in their original sequence index.
    - The output is JSON-serialized with `indent=2`, `sort_keys=False`
      (we preserve our own deterministic order via the tuple sort below),
      and `ensure_ascii=False`.
    - A trailing newline is appended so `git diff` shows clean edits.

Usage:

    # Regenerate the snapshot in place:
    python3 scripts/snapshot-workflow-steps.py

    # Compare against committed snapshot (used by `make workflow-steps-check`):
    python3 scripts/snapshot-workflow-steps.py --check

Exit codes:
    0 — snapshot written (or in --check mode, no drift)
    1 — drift detected in --check mode
    2 — environmental error (missing PyYAML, missing workflows dir, etc.)

Cross-reference: see `tokenpak/_snapshots/README.md` for the ratchet protocol
and the `removes-ci-step:` PR-body declaration format.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SNAPSHOT_PATH = REPO_ROOT / "tokenpak" / "_snapshots" / "workflow-steps.json"


def _load_yaml(path: pathlib.Path) -> dict:
    """Load a YAML file. Imported locally so absence of PyYAML produces a
    targeted error message rather than a generic ImportError on module load."""
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "ERROR: PyYAML is required to run snapshot-workflow-steps.py.\n"
            "       Install via: pip install pyyaml  (or use the [dev] extra)",
            file=sys.stderr,
        )
        sys.exit(2)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_snapshot() -> list[dict]:
    """Walk .github/workflows/*.yml and produce a sorted list of step rows.

    Schema per row:
        {
          "workflow": "<filename.yml>",
          "job": "<job-id>",
          "step_idx": <int>,               # position within job (0-based)
          "step_id": <str | null>,          # step's `id:` (if set; else null)
          "step_name": <str>                # step's `name:` (or `uses:` if no name)
        }
    """
    if not WORKFLOWS_DIR.is_dir():
        print(f"ERROR: workflows dir not found: {WORKFLOWS_DIR}", file=sys.stderr)
        sys.exit(2)

    snapshot: list[dict] = []

    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        data = _load_yaml(wf_path)
        jobs = data.get("jobs") or {}
        # Iterate jobs in source order (Python 3.7+ preserves dict insertion order;
        # PyYAML's safe_load uses regular dict which respects that).
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps") or []
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                # Derive a step name: prefer `name:`; fall back to `uses:` (action
                # ref like `actions/checkout@v4`); else "<unnamed>".
                name = step.get("name") or step.get("uses") or "<unnamed>"
                snapshot.append(
                    {
                        "workflow": wf_path.name,
                        "job": job_id,
                        "step_idx": idx,
                        "step_id": step.get("id"),
                        "step_name": name,
                    }
                )

    # Final deterministic sort: by (workflow, job, step_idx). Steps within
    # a job remain in their source order via step_idx.
    snapshot.sort(key=lambda row: (row["workflow"], row["job"], row["step_idx"]))
    return snapshot


def write_snapshot(snapshot: list[dict]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False)
    SNAPSHOT_PATH.write_text(payload + "\n", encoding="utf-8")


def check_snapshot(snapshot: list[dict]) -> int:
    """Compare freshly-generated snapshot against the committed file.
    Returns 0 if identical, 1 if drift."""
    if not SNAPSHOT_PATH.exists():
        print(
            f"ERROR: committed snapshot missing: {SNAPSHOT_PATH}\n"
            "       Run `make workflow-steps-snapshot` and commit the result.",
            file=sys.stderr,
        )
        return 1
    expected = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    actual = SNAPSHOT_PATH.read_text(encoding="utf-8")
    if expected == actual:
        return 0
    print(
        "ERROR: workflow-steps snapshot is out of date.\n"
        "       The committed snapshot does not match the current workflows.\n"
        "\n"
        "       To fix: run `make workflow-steps-snapshot` and commit the result.\n"
        "\n"
        "       If your PR intentionally REMOVES a workflow step, you must\n"
        "       additionally declare `removes-ci-step: <step.id>` (one line per\n"
        "       removed step) in your PR body. See tokenpak/_snapshots/README.md\n"
        "       for the ratchet protocol.\n",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against committed snapshot; fail (exit 1) if drift detected",
    )
    args = parser.parse_args()

    snapshot = build_snapshot()

    if args.check:
        return check_snapshot(snapshot)

    write_snapshot(snapshot)
    print(f"wrote {len(snapshot)} steps to {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
