#!/usr/bin/env python3
"""Verify that every selected CI job ran and every unselected job skipped."""

from __future__ import annotations

import json
import os
import sys
from typing import Mapping

JOB_NAMES = (
    "python_proxy",
    "full_python",
    "integration_chaos",
    "determinism_benchmarks",
    "packaging_dependencies",
    "migration_storage",
    "pricing_billing",
    "docs_claims",
    "javascript_sdk",
    "workflow_release",
)

VALID_RESULTS = {"success", "failure", "cancelled", "skipped"}


def parse_bool(value: str, *, label: str) -> bool:
    """Parse a strict lower-case boolean emitted by the classifier."""

    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{label} is missing or malformed: {value!r}")


def verify_selected_jobs(
    *,
    classifier_ok: bool,
    full_conservative: bool,
    unknown: bool,
    changes_result: str,
    trust_baseline_result: str,
    selections: Mapping[str, bool],
    results: Mapping[str, str],
) -> list[str]:
    """Return every contract violation; an empty list means acceptance."""

    errors: list[str] = []
    if not classifier_ok:
        errors.append("classifier reported an error")
    if changes_result != "success":
        errors.append(f"changes job result is {changes_result!r}, expected 'success'")
    if trust_baseline_result != "success":
        errors.append(f"trust-baseline job result is {trust_baseline_result!r}, expected 'success'")
    if unknown and not full_conservative:
        errors.append("unknown changes did not select full-conservative coverage")

    for name in JOB_NAMES:
        if name not in selections:
            errors.append(f"selection for {name!r} is missing")
            continue
        if name not in results:
            errors.append(f"result for {name!r} is missing")
            continue
        result = results[name]
        if result not in VALID_RESULTS:
            errors.append(f"result for {name!r} is malformed: {result!r}")
            continue
        selected = selections[name]
        if full_conservative and not selected:
            errors.append(f"full-conservative coverage did not select {name!r}")
        if selected and result != "success":
            errors.append(f"selected job {name!r} finished as {result!r}")
        if not selected and result != "skipped":
            errors.append(f"unselected job {name!r} ran with result {result!r}")
    return errors


def _from_environment() -> tuple[dict[str, bool], dict[str, str]]:
    selections: dict[str, bool] = {}
    results: dict[str, str] = {}
    for name in JOB_NAMES:
        env_name = name.upper()
        selections[name] = parse_bool(
            os.environ.get(f"SELECTED_{env_name}", ""), label=f"SELECTED_{env_name}"
        )
        results[name] = os.environ.get(f"RESULT_{env_name}", "")
    return selections, results


def main() -> int:
    try:
        selections, results = _from_environment()
        errors = verify_selected_jobs(
            classifier_ok=parse_bool(os.environ.get("CLASSIFIER_OK", ""), label="CLASSIFIER_OK"),
            full_conservative=parse_bool(
                os.environ.get("FULL_CONSERVATIVE", ""), label="FULL_CONSERVATIVE"
            ),
            unknown=parse_bool(os.environ.get("UNKNOWN", ""), label="UNKNOWN"),
            changes_result=os.environ.get("CHANGES_RESULT", ""),
            trust_baseline_result=os.environ.get("TRUST_BASELINE_RESULT", ""),
            selections=selections,
            results=results,
        )
    except ValueError as exc:
        errors = [str(exc)]

    report = {"schema": "tokenpak-ci-required/v1", "ok": not errors, "errors": errors}
    print(json.dumps(report, indent=2, sort_keys=True))
    if errors:
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        return 1
    print("All classifier-selected jobs ran and passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
