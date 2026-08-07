"""Contract tests for the always-running selected-job aggregator."""

from __future__ import annotations

import pytest

from scripts.ci.verify_selected_jobs import JOB_NAMES, parse_bool, verify_selected_jobs


def _selections(value: bool) -> dict[str, bool]:
    return {name: value for name in JOB_NAMES}


def _results(value: str) -> dict[str, str]:
    return {name: value for name in JOB_NAMES}


def test_full_conservative_success_is_accepted() -> None:
    errors = verify_selected_jobs(
        classifier_ok=True,
        full_conservative=True,
        unknown=False,
        changes_result="success",
        trust_baseline_result="success",
        selections=_selections(True),
        results=_results("success"),
    )

    assert errors == []


def test_selected_skipped_job_is_rejected() -> None:
    results = _results("success")
    results["javascript_sdk"] = "skipped"

    errors = verify_selected_jobs(
        classifier_ok=True,
        full_conservative=True,
        unknown=False,
        changes_result="success",
        trust_baseline_result="success",
        selections=_selections(True),
        results=results,
    )

    assert "selected job 'javascript_sdk' finished as 'skipped'" in errors


def test_unselected_running_job_is_rejected() -> None:
    selections = _selections(False)
    results = _results("skipped")
    results["docs_claims"] = "success"

    errors = verify_selected_jobs(
        classifier_ok=True,
        full_conservative=False,
        unknown=False,
        changes_result="success",
        trust_baseline_result="success",
        selections=selections,
        results=results,
    )

    assert "unselected job 'docs_claims' ran with result 'success'" in errors


def test_classifier_and_baseline_failures_are_rejected() -> None:
    errors = verify_selected_jobs(
        classifier_ok=False,
        full_conservative=True,
        unknown=True,
        changes_result="success",
        trust_baseline_result="failure",
        selections=_selections(True),
        results=_results("success"),
    )

    assert "classifier reported an error" in errors
    assert any("trust-baseline" in error for error in errors)


def test_unknown_without_full_coverage_is_rejected() -> None:
    errors = verify_selected_jobs(
        classifier_ok=True,
        full_conservative=False,
        unknown=True,
        changes_result="success",
        trust_baseline_result="success",
        selections=_selections(False),
        results=_results("skipped"),
    )

    assert "unknown changes did not select full-conservative coverage" in errors


def test_full_conservative_missing_selection_is_rejected() -> None:
    selections = _selections(True)
    results = _results("success")
    selections["docs_claims"] = False
    results["docs_claims"] = "skipped"

    errors = verify_selected_jobs(
        classifier_ok=True,
        full_conservative=True,
        unknown=False,
        changes_result="success",
        trust_baseline_result="success",
        selections=selections,
        results=results,
    )

    assert "full-conservative coverage did not select 'docs_claims'" in errors


def test_parse_bool_is_strict() -> None:
    assert parse_bool("true", label="value") is True
    assert parse_bool("false", label="value") is False
    with pytest.raises(ValueError, match="malformed"):
        parse_bool("True", label="value")
