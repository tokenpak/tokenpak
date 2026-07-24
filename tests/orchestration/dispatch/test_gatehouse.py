"""Tests for the deterministic Gatehouse (Standards Delta v0 §5.7).

Verifies:

  * each deterministic check has a pass case and a fail case
    (manifest_completeness, route/station schema validity, acceptance-criteria
    presence, station output schema validity, permission constraints, delivery
    package completeness);
  * the Gatehouse runs no LLM (it is structural-only);
  * the Reviewer → Gatehouse handoff: reviewer pass → delivery_ready; warning +
    accept → delivery_ready_with_warning; warning + reject → blocked; warning
    unresolved → decision_required (DispatchDecision created); fail → blocked
    with required_fixes and no repair loop;
  * a failed structural check blocks regardless of reviewer verdict;
  * the §5.7 cost note is attached when the route used a Reviewer Station.
"""

from __future__ import annotations

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.gatehouse import (
    REVIEWER_COST_NOTE,
    DeliveryStatus,
    Gatehouse,
    GatehouseReport,
)
from tokenpak.orchestration.dispatch.models.decision import DispatchDecision
from tokenpak.orchestration.dispatch.models.manifest import DispatchManifest
from tokenpak.orchestration.dispatch.models.route import DispatchRoute
from tokenpak.orchestration.dispatch.models.station_run import DispatchStationRun
from tokenpak.orchestration.dispatch.stations.reviewer import ReviewerStationResult

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> DispatchManifest:
    data = dict(
        id="manifest_01",
        job_id="job_01",
        route_id="route.code_task.v1",
        goal="implement the flag",
        acceptance_criteria=[{"id": "ac1", "description": "adds the flag"}],
        constraints=[],
        permissions={"autonomy_mode": "dispatch_with_approval"},
        quality_requirements={
            "test_required": True,
            "review_required": True,
            "docs_required": False,
            "evidence_required": True,
        },
        status="active",
    )
    data.update(overrides)
    return DispatchManifest(**data)


def _route(**overrides) -> DispatchRoute:
    data = dict(
        id="route.code_task.v1",
        name="code_task",
        description="Code task route",
        default_risk="medium",
        stations=[
            {
                "id": "build",
                "required_role": "builder",
                "required_capabilities": ["code_drafting", "patch_generation"],
                "output_schema": "station_result.v1",
            },
            {
                "id": "review",
                "required_role": "reviewer",
                "required_capabilities": ["semantic_review"],
                "output_schema": "reviewer_result.v1",
            },
        ],
    )
    data.update(overrides)
    return DispatchRoute(**data)


def _station_run(status="completed", payload=None, schema="station_result.v1"):
    return DispatchStationRun(
        id="stationrun_01",
        run_id="run_01",
        station_id="build",
        worker_id="worker.builder.default.v1",
        context_bundle_id="ctx_01",
        status=status,
        result_payload=payload if payload is not None else {"ok": True},
        result_schema_version=schema,
    )


def _full_delivery_fields() -> dict:
    """All five route-required delivery pieces present and non-empty."""

    return {
        "summary": "did the thing",
        "files_changed": ["src/app.py"],
        "tests": ["tests/test_app.py"],
        "risks": ["touches cli"],
        "next_steps": ["ship it"],
    }


def _reviewer_result(status="pass", required_fixes=None) -> ReviewerStationResult:
    return ReviewerStationResult.for_status(
        status,
        required_fixes=required_fixes,
        reason=f"{status} verdict",
    )


# ---------------------------------------------------------------------------
# Individual deterministic checks — pass + fail each
# ---------------------------------------------------------------------------


def test_manifest_completeness_pass():
    res = Gatehouse().check_manifest_completeness(_manifest())
    assert res.passed and res.name == "manifest_completeness"


def test_manifest_completeness_fail_missing_goal():
    res = Gatehouse().check_manifest_completeness(_manifest(goal=""))
    assert not res.passed
    assert "goal" in res.detail


def test_route_station_schema_pass():
    res = Gatehouse().check_route_station_schema(_route())
    assert res.passed and res.name == "route_station_schema"


def test_route_station_schema_fail_on_invalid_mapping():
    # required_capabilities references an unknown capability → model validation
    # fails → structural check fails.
    bad = {
        "id": "route.bad.v1",
        "name": "bad",
        "description": "bad",
        "default_risk": "low",
        "stations": [
            {
                "id": "s",
                "required_role": "builder",
                "required_capabilities": ["not_a_capability"],
                "output_schema": "x.v1",
            }
        ],
    }
    res = Gatehouse().check_route_station_schema(bad)
    assert not res.passed


def test_route_station_schema_fail_station_role_and_component_both_set():
    route = _route(
        stations=[
            {
                "id": "build",
                "required_role": "builder",
                "system_component": "delivery_dock",
                "required_capabilities": ["code_drafting"],
                "output_schema": "station_result.v1",
            }
        ]
    )
    res = Gatehouse().check_route_station_schema(route)
    assert not res.passed
    assert "exactly one" in res.detail


def test_acceptance_criteria_presence_pass():
    res = Gatehouse().check_acceptance_criteria_presence(_manifest())
    assert res.passed


def test_acceptance_criteria_presence_fail_when_empty():
    res = Gatehouse().check_acceptance_criteria_presence(_manifest(acceptance_criteria=[]))
    assert not res.passed


def test_station_output_schema_pass():
    res = Gatehouse().check_station_output_schema([_station_run()])
    assert res.passed


def test_station_output_schema_pass_with_injected_validator():
    def validator(payload):
        assert "ok" in payload  # noqa: S101 - test validator

    res = Gatehouse().check_station_output_schema(
        [_station_run()], validators={"station_result.v1": validator}
    )
    assert res.passed


def test_station_output_schema_fail_completed_without_payload():
    # A completed station with no result_payload is a structural fail.
    run = _station_run().model_copy(update={"result_payload": None})
    res = Gatehouse().check_station_output_schema([run])
    assert not res.passed


def test_station_output_schema_fail_injected_validator_rejects():
    def validator(payload):
        raise ValueError("missing key")

    res = Gatehouse().check_station_output_schema(
        [_station_run()], validators={"station_result.v1": validator}
    )
    assert not res.passed
    assert "validation" in res.detail


def test_station_output_schema_ignores_failed_stations():
    # A failed station is not required to carry output.
    res = Gatehouse().check_station_output_schema([_station_run(status="failed", payload=None)])
    assert res.passed


def test_permission_constraints_pass():
    res = Gatehouse().check_permission_constraints(_manifest())
    assert res.passed


def test_permission_constraints_fail_on_contradiction():
    m = _manifest(
        permissions={
            "autonomy_mode": "dispatch_with_approval",
            "allowed_actions": ["edit_file"],
            "forbidden_actions": ["edit_file"],
        }
    )
    res = Gatehouse().check_permission_constraints(m)
    assert not res.passed
    assert "allowed and forbidden" in res.detail


def test_delivery_package_completeness_pass():
    res = Gatehouse().check_delivery_package_completeness(_route(), _full_delivery_fields())
    assert res.passed


def test_delivery_package_completeness_fail_missing_piece():
    fields = _full_delivery_fields()
    del fields["tests"]
    res = Gatehouse().check_delivery_package_completeness(_route(), fields)
    assert not res.passed
    assert "tests" in res.detail


def test_delivery_package_completeness_dynamic_respects_route_flags():
    # When a route does not require tests, an absent tests piece is fine.
    route = _route(delivery={"include_tests": False})
    fields = _full_delivery_fields()
    del fields["tests"]
    res = Gatehouse().check_delivery_package_completeness(route, fields)
    assert res.passed


# ---------------------------------------------------------------------------
# Full battery
# ---------------------------------------------------------------------------


def test_run_checks_all_pass():
    report = Gatehouse().run_checks(
        manifest=_manifest(),
        route=_route(),
        station_runs=[_station_run()],
        delivery_package_fields=_full_delivery_fields(),
    )
    assert isinstance(report, GatehouseReport)
    assert report.passed
    assert {c.name for c in report.checks} == {
        "manifest_completeness",
        "route_station_schema",
        "acceptance_criteria_presence",
        "station_output_schema",
        "permission_constraints",
        "delivery_package_completeness",
    }


def test_run_checks_collects_failures():
    report = Gatehouse().run_checks(
        manifest=_manifest(goal="", acceptance_criteria=[]),
        route=_route(),
        station_runs=[_station_run()],
        delivery_package_fields=_full_delivery_fields(),
    )
    assert not report.passed
    failed = {c.name for c in report.failures}
    assert "manifest_completeness" in failed
    assert "acceptance_criteria_presence" in failed


# ---------------------------------------------------------------------------
# Reviewer → Gatehouse handoff (§5.7 table)
# ---------------------------------------------------------------------------


def _evaluate(reviewer_result, *, warning_decision_resolution=None, report=None):
    gh = Gatehouse()
    return gh.evaluate_delivery(
        job_id="job_01",
        manifest=_manifest(),
        route=_route(),
        reviewer_result=reviewer_result,
        report=report,
        station_runs=[_station_run()],
        delivery_package_fields=_full_delivery_fields(),
        warning_decision_resolution=warning_decision_resolution,
    )


def test_handoff_reviewer_pass_delivery_ready():
    pkg = _evaluate(_reviewer_result("pass"))
    assert pkg.status is DeliveryStatus.DELIVERY_READY
    assert pkg.gatehouse_report.passed
    assert pkg.decision is None
    assert pkg.required_fixes == []
    # Cost note attached because the route used a Reviewer Station.
    assert pkg.cost_note == REVIEWER_COST_NOTE


def test_handoff_reviewer_fail_blocked_with_required_fixes_no_repair_loop():
    fixes = [
        {
            "severity": "high",
            "description": "implement the flag",
            "suggested_station": "build",
        }
    ]
    pkg = _evaluate(_reviewer_result("fail", required_fixes=fixes))
    assert pkg.status is DeliveryStatus.BLOCKED
    assert len(pkg.required_fixes) == 1
    assert pkg.required_fixes[0].severity.value == "high"
    assert "no automatic repair loop" in pkg.summary
    assert pkg.cost_note == REVIEWER_COST_NOTE


def test_handoff_reviewer_warning_unresolved_creates_decision():
    pkg = _evaluate(_reviewer_result("warning"), warning_decision_resolution=None)
    assert pkg.status is DeliveryStatus.DECISION_REQUIRED
    assert isinstance(pkg.decision, DispatchDecision)
    option_ids = {o.id for o in pkg.decision.options}
    assert option_ids == {"accept", "reject"}
    assert pkg.decision.job_id == "job_01"


def test_handoff_reviewer_warning_accept_ready_with_warning():
    pkg = _evaluate(_reviewer_result("warning"), warning_decision_resolution=True)
    assert pkg.status is DeliveryStatus.DELIVERY_READY_WITH_WARNING
    assert pkg.decision is None


def test_handoff_reviewer_warning_reject_blocked():
    pkg = _evaluate(_reviewer_result("warning"), warning_decision_resolution=False)
    assert pkg.status is DeliveryStatus.BLOCKED


def test_failed_structural_check_blocks_regardless_of_reviewer_pass():
    # Reviewer says pass, but a structural check fails → Gatehouse blocks.
    gh = Gatehouse()
    pkg = gh.evaluate_delivery(
        job_id="job_01",
        manifest=_manifest(acceptance_criteria=[]),  # structural fail
        route=_route(),
        reviewer_result=_reviewer_result("pass"),
        station_runs=[_station_run()],
        delivery_package_fields=_full_delivery_fields(),
    )
    assert pkg.status is DeliveryStatus.BLOCKED
    assert not pkg.gatehouse_report.passed
    assert "structural check" in pkg.summary


def test_cost_note_absent_when_route_has_no_reviewer():
    gh = Gatehouse()
    pkg = gh.evaluate_delivery(
        job_id="job_01",
        manifest=_manifest(),
        route=_route(),
        reviewer_result=_reviewer_result("pass"),
        station_runs=[_station_run()],
        delivery_package_fields=_full_delivery_fields(),
        route_uses_reviewer=False,
    )
    assert pkg.cost_note is None


def test_evaluate_delivery_runs_checks_when_report_not_supplied():
    pkg = _evaluate(_reviewer_result("pass"), report=None)
    assert pkg.gatehouse_report.checks  # battery ran
    assert pkg.status is DeliveryStatus.DELIVERY_READY
