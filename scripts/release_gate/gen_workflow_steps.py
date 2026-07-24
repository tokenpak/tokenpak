#!/usr/bin/env python3
"""gen_workflow_steps.py — generate tokenpak/_snapshots/workflow-steps.json.

Per Std 21 §12 + Std 30 §13.3 (R11 workflow-step ratchet). Walks
`.github/workflows/release*.yml` and `release-rehearsal.yml`, emits a sorted
list of `(workflow, job, step.id, step.name)` tuples.

PRs that remove a step MUST declare `removes-ci-step: <step.id>` in PR body
per Std 21 §12. This snapshot enables CI to detect undeclared step removals.

Usage:
    python3 scripts/release_gate/gen_workflow_steps.py [--check] [--out PATH]

Authority: Std 21 §12 + Std 30 §13.3, ratified 2026-05-09.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "tokenpak" / "_snapshots" / "workflow-steps.json"
GUARDED_GLOBS = ["release*.yml", "release-rehearsal.yml"]


def load_yaml_safe(path: Path) -> dict | None:
    """Minimal YAML parsing without requiring PyYAML at build time. Falls back
    to a pure-Python parser via ruamel.yaml or PyYAML if available; otherwise
    extracts steps via line-based regex (lossy but sufficient for the snapshot)."""
    try:
        import yaml

        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML

        return YAML(typ="safe").load(path.read_text())
    except ImportError:
        pass
    # Fallback: regex-based step extraction
    return None


def extract_steps_regex(path: Path) -> list[dict[str, str]]:
    """Lossy fallback: extract step names + ids by line scanning. Used only
    when YAML libs aren't available."""
    import re

    steps = []
    current_job = "?"
    text = path.read_text()
    job_re = re.compile(r"^([a-zA-Z][\w-]*):\s*$", re.MULTILINE)
    name_re = re.compile(r"^\s*-\s+name:\s+(.+)$", re.MULTILINE)
    id_re = re.compile(r"^\s*id:\s+(.+)$", re.MULTILINE)
    for m in re.finditer(r"^\s*-\s+name:\s+([^\n]+)\n((?:\s+\S+:\s*\S.*\n)+)", text, re.MULTILINE):
        name = m.group(1).strip()
        body = m.group(2)
        sid = ""
        for sm in re.finditer(r"^\s*id:\s+(\S+)", body, re.MULTILINE):
            sid = sm.group(1).strip()
            break
        steps.append({"workflow": path.name, "job": current_job, "id": sid, "name": name})
    return steps


def extract_steps(workflow: dict, workflow_filename: str) -> list[dict[str, str]]:
    steps = []
    jobs = workflow.get("jobs", {}) or {}
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    "workflow": workflow_filename,
                    "job": job_name,
                    "id": step.get("id", "") or "",
                    "name": step.get("name", "") or "",
                }
            )
    return steps


def extract_job_guards(workflow: dict, workflow_filename: str) -> list[dict[str, str]]:
    """Capture each job's job-level ``if:`` guard (Std 12 §3.3 dispatch ratchet).

    The step ratchet above only captures step names/ids, so a regression that
    *weakens a job guard* — e.g. dropping the ``github.event_name == 'push'``
    term from the ``build`` / ``release`` jobs back to a ref-only condition,
    which would re-open the dispatch-at-tag GitHub-Release spoofing hole — would
    pass ``workflow-steps-check`` silently. Capturing the normalized ``if:``
    string for every guarded job closes that blind spot: the regression changes
    the captured string and trips the snapshot check. Requires a structured YAML
    parse; ``pyyaml`` is a core runtime dependency so this is always available
    wherever the snapshot is generated or checked.
    """
    guards = []
    jobs = workflow.get("jobs", {}) or {}
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        cond = job.get("if")
        if cond is None:
            continue
        guards.append(
            {
                "workflow": workflow_filename,
                "job": job_name,
                "if": str(cond).strip(),
            }
        )
    return guards


def build_snapshot() -> dict:
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    all_steps = []
    all_guards = []
    seen_paths: set[Path] = set()
    for glob in GUARDED_GLOBS:
        for wf_path in sorted(workflows_dir.glob(glob)):
            # ``release-rehearsal.yml`` matches both ``release*.yml`` and the
            # explicit rehearsal glob. A workflow is one ratchet subject even
            # when several guarded patterns select it.
            if wf_path in seen_paths:
                continue
            seen_paths.add(wf_path)
            data = load_yaml_safe(wf_path)
            if data is None:
                steps = extract_steps_regex(wf_path)
                # Job-level guard capture needs a structured parse. pyyaml is a
                # core runtime dependency, so this fallback is effectively dead;
                # if it is ever hit, the guard ratchet degrades to empty rather
                # than emitting environment-dependent partial data.
                guards = []
            else:
                steps = extract_steps(data, wf_path.name)
                guards = extract_job_guards(data, wf_path.name)
            all_steps.extend(steps)
            all_guards.extend(guards)
    # Sort by (workflow, job, id, name) for deterministic diff
    all_steps.sort(key=lambda s: (s["workflow"], s["job"], s["id"], s["name"]))
    all_guards.sort(key=lambda g: (g["workflow"], g["job"], g["if"]))
    return {
        "version": "1.1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "guarded_globs": GUARDED_GLOBS,
        "steps": all_steps,
        "job_guards": all_guards,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate workflow-steps snapshot")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    snapshot = build_snapshot()
    body = json.dumps(snapshot, indent=2) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.check:
        if not args.out.exists():
            print(f"workflow-steps.json missing at {args.out}", file=sys.stderr)
            return 1
        try:
            on_disk = json.loads(args.out.read_text())
        except Exception as e:
            print(f"on-disk snapshot is not valid JSON: {e}", file=sys.stderr)
            return 1
        on_disk_steps = {
            (s["workflow"], s["job"], s["id"], s["name"]) for s in on_disk.get("steps", [])
        }
        new_steps = {(s["workflow"], s["job"], s["id"], s["name"]) for s in snapshot["steps"]}
        added = sorted(new_steps - on_disk_steps)
        removed = sorted(on_disk_steps - new_steps)

        # Std 12 §3.3 dispatch-guard ratchet: a changed job `if:` shows up as a
        # removed old guard tuple + an added new one, so a ref-only regression
        # on build/release trips this check.
        on_disk_guards = {(g["workflow"], g["job"], g["if"]) for g in on_disk.get("job_guards", [])}
        new_guards = {(g["workflow"], g["job"], g["if"]) for g in snapshot["job_guards"]}
        guard_added = sorted(new_guards - on_disk_guards)
        guard_removed = sorted(on_disk_guards - new_guards)

        if added or removed or guard_added or guard_removed:
            print("workflow-steps snapshot drift detected:", file=sys.stderr)
            for w, j, i, n in added:
                print(f"  + [{w}][{j}] id={i} name={n}", file=sys.stderr)
            for w, j, i, n in removed:
                print(f"  - [{w}][{j}] id={i} name={n}", file=sys.stderr)
            for w, j, cond in guard_added:
                print(f"  + guard [{w}][{j}] if={cond}", file=sys.stderr)
            for w, j, cond in guard_removed:
                print(f"  - guard [{w}][{j}] if={cond}", file=sys.stderr)
            print(
                "\nIf intentional: run `make workflow-steps-snapshot` and commit; step removals require",
                file=sys.stderr,
            )
            print(
                "`removes-ci-step: <step.id>` in PR body per Std 21 §12, and a weakened release",
                file=sys.stderr,
            )
            print("dispatch guard must cite Std 12 §3.3 review.", file=sys.stderr)
            return 1
        print("workflow-steps snapshot matches on-disk", file=sys.stderr)
        return 0

    args.out.write_text(body)
    print(
        f"workflow-steps snapshot written: {args.out} ({len(snapshot['steps'])} steps)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
