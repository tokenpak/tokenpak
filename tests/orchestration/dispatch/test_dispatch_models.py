"""Tests for the Dispatch record schemas (P-SCHEMA-01).

Verifies the twelve Dispatch records against Standards Delta v0 §4–§6:
  * every record round-trips through its generated JSON Schema (build + parse);
  * status enums on DispatchJob (§4.1/§6) and DispatchStationRun (§4.5) match
    the Standards Delta verbatim;
  * the Std 41 crosswalk hook, path_policy invariants, DispatchEffect file-state
    cases, contract strictness, and the *Pak-suffix guard.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Dispatch is pydantic-native and round-trips records through jsonschema; both
# deps ship via the opt-in `dispatch` extra (pyproject
# [project.optional-dependencies]). Skip cleanly on slim installs that lack
# them rather than erroring at collection time.
pytest.importorskip("jsonschema")
pytest.importorskip("pydantic")

import jsonschema
from pydantic import BaseModel, ValidationError

from tokenpak.orchestration.dispatch.models import (
    DISPATCH_RECORD_MODELS,
    DispatchArtifact,
    DispatchDecision,
    DispatchEffect,
    DispatchJob,
    DispatchManifest,
    DispatchPolicy,
    DispatchReceipt,
    DispatchRoute,
    DispatchRun,
    DispatchStationRun,
    DispatchWorker,
    LateResult,
    PakSuffixCollisionError,
    PathPolicy,
    _assert_no_pak_suffix,
)
from tokenpak.orchestration.dispatch.models.common import MANDATORY_DENIED_PATHS
from tokenpak.orchestration.dispatch.schemas import generate_schemas, load_schema

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _valid_instances() -> dict[str, BaseModel]:
    """Return one minimal-valid instance for each of the twelve records."""

    return {
        "DispatchJob": DispatchJob(
            id="job_01",
            created_at=_NOW,
            raw_request="add a CLI flag",
            detected_intent="code_task",
            autonomy_mode="dispatch_with_approval",
            status="draft",
        ),
        "DispatchManifest": DispatchManifest(
            id="manifest_01",
            job_id="job_01",
            route_id="route.code_task.v1",
            goal="implement the flag",
            permissions={"autonomy_mode": "dispatch_with_approval"},
            quality_requirements={
                "test_required": True,
                "review_required": True,
                "docs_required": False,
                "evidence_required": True,
            },
            status="draft",
        ),
        "DispatchRoute": DispatchRoute(
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
                }
            ],
        ),
        "DispatchRun": DispatchRun(
            id="run_01",
            job_id="job_01",
            manifest_id="manifest_01",
            route_id="route.code_task.v1",
            started_at=_NOW,
            status="running",
        ),
        "DispatchStationRun": DispatchStationRun(
            id="stationrun_01",
            run_id="run_01",
            station_id="build",
            worker_id="worker.builder.default.v1",
            context_bundle_id="ctx_01",
            status="completed",
            result_schema_version="station_result.v1",
        ),
        "DispatchDecision": DispatchDecision(
            id="decision_01",
            job_id="job_01",
            created_at=_NOW,
            scope="job",
            title="Pick approach",
            question="Which approach?",
            reason="ambiguous request",
            risk_level="medium",
            options=[{"id": "a", "label": "A", "description": "first", "tradeoffs": []}],
            recommendation={"option_id": "a", "rationale": "simplest"},
            default_action={"option_id": "a"},
            status="pending",
        ),
        "DispatchReceipt": DispatchReceipt(
            id="receipt_01",
            job_id="job_01",
            run_id="run_01",
            route_id="route.code_task.v1",
            final_status="delivered",
            created_at=_NOW,
        ),
        "DispatchEffect": DispatchEffect(
            id="effect_01",
            job_id="job_01",
            station_run_id="stationrun_01",
            tool_name="apply_patch",
            target_type="file",
            target="src/app.py",
            before_exists=False,
            after_hash="deadbeef",
            rollback_behavior="delete_file_if_after_hash_matches",
            status="planned",
            created_at=_NOW,
        ),
        "LateResult": LateResult(
            id="late_01",
            job_id="job_01",
            station_run_id="stationrun_01",
            received_at=_NOW,
            result_hash="abc123",
        ),
        "DispatchArtifact": DispatchArtifact(
            id="artifact_01",
            job_id="job_01",
            kind="patch",
            target="patches/01.diff",
            content_hash="ffff",
            created_at=_NOW,
        ),
        "DispatchWorker": DispatchWorker(
            id="worker.builder.default.v1",
            roles=["builder"],
            capabilities=["code_drafting", "patch_generation"],
            input_schema="station_input.v1",
            output_schema="station_result.v1",
            default_loop_policy={
                "max_iterations": 3,
                "max_tool_calls": 8,
                "max_wall_seconds": 900,
            },
            permission_profile={},
        ),
        "DispatchPolicy": DispatchPolicy(
            id="policy.default.v1",
            name="default",
            autonomy_mode="draft",
        ),
    }


def test_registry_has_twelve_records():
    assert len(DISPATCH_RECORD_MODELS) == 12
    # All keys map to Pydantic BaseModel subclasses.
    for name, model in DISPATCH_RECORD_MODELS.items():
        assert issubclass(model, BaseModel), name


def test_instance_factory_covers_every_record():
    # Guards against the round-trip test silently skipping a record.
    assert set(_valid_instances()) == set(DISPATCH_RECORD_MODELS)


@pytest.mark.parametrize("name", sorted(DISPATCH_RECORD_MODELS))
def test_model_round_trips_through_json_schema(name):
    """Build → dump → validate against JSON Schema → parse back (every record)."""

    instance = _valid_instances()[name]
    model = DISPATCH_RECORD_MODELS[name]

    # Build: generated schema must itself be a valid Draft 2020-12 schema.
    schema = model.model_json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)

    # The committed on-disk schema matches the live model.
    assert load_schema(name) == generate_schemas()[name]

    # Dumped JSON instance validates against the schema.
    dumped = instance.model_dump(mode="json")
    jsonschema.validate(instance=dumped, schema=schema)

    # Parse: round-trips back to an equal model.
    assert model.model_validate(dumped) == instance


def test_dispatch_job_status_enum_matches_standards_delta():
    """DispatchJob.status enum is the exact §4.1/§6 12-state list."""

    from tokenpak.orchestration.dispatch.models.enums import DispatchJobStatus

    expected = [
        "draft",
        "manifest_ready",
        "dispatched",
        "running",
        "gate_review",
        "blocked",
        "repairing",
        "delivery_ready",
        "delivered",
        "cancelled",
        "failed",
        "withdrawn",
    ]
    assert [s.value for s in DispatchJobStatus] == expected


def test_station_run_status_enum_matches_standards_delta():
    """DispatchStationRun.status enum is the exact §4.5 9-state list."""

    from tokenpak.orchestration.dispatch.models.enums import StationRunStatus

    expected = [
        "queued",
        "context_ready",
        "running",
        "completed",
        "failed",
        "failed_interrupted",
        "needs_recovery",
        "cancelled",
        "skipped",
    ]
    assert [s.value for s in StationRunStatus] == expected


def test_dispatch_job_source_task_packet_id_is_nullable_crosswalk_hook():
    """Std 41 crosswalk hook present and defaults to None (standalone job)."""

    job = _valid_instances()["DispatchJob"]
    assert job.source_task_packet_id is None
    linked = job.model_copy(update={"source_task_packet_id": "TASK-123"})
    assert linked.source_task_packet_id == "TASK-123"


def test_path_policy_always_includes_mandatory_denied_paths():
    """denied_paths always contains .env, .git/**, secrets/**, license/**."""

    # Default construction.
    default_policy = PathPolicy()
    for mandatory in MANDATORY_DENIED_PATHS:
        assert mandatory in default_policy.denied_paths
    assert default_policy.allow_new_files is True
    assert default_policy.allow_delete_files is False

    # Caller-supplied denied_paths missing the mandatory globs gets them injected.
    custom = PathPolicy(denied_paths=["build/**"])
    assert "build/**" in custom.denied_paths
    for mandatory in MANDATORY_DENIED_PATHS:
        assert mandatory in custom.denied_paths


def test_dispatch_effect_three_file_state_cases():
    """DispatchEffect covers create / modify / delete cases (§4.8)."""

    create = DispatchEffect(
        id="effect_create",
        job_id="job_01",
        station_run_id="sr_01",
        tool_name="apply_patch",
        target_type="file",
        target="new.py",
        before_exists=False,
        before_hash=None,
        after_hash="hash_after",
        rollback_behavior="delete_file_if_after_hash_matches",
        status="applied",
        created_at=_NOW,
    )
    assert create.before_exists is False and create.before_hash is None

    modify = DispatchEffect(
        id="effect_modify",
        job_id="job_01",
        station_run_id="sr_01",
        tool_name="apply_patch",
        target_type="file",
        target="existing.py",
        before_exists=True,
        before_hash="hash_before",
        after_hash="hash_after",
        rollback_behavior="restore_before_content_if_current_hash_matches_after_hash",
        status="applied",
        created_at=_NOW,
    )
    assert modify.before_hash and modify.after_hash

    delete = DispatchEffect(
        id="effect_delete",
        job_id="job_01",
        station_run_id="sr_01",
        tool_name="apply_patch",
        target_type="file",
        target="gone.py",
        before_exists=True,
        before_hash="hash_before",
        after_hash=None,
        rollback_behavior="restore_before_content",
        status="applied",
        created_at=_NOW,
    )
    assert delete.after_hash is None


def test_records_forbid_unknown_fields():
    """extra='forbid' makes the schemas strict contracts."""

    with pytest.raises(ValidationError):
        DispatchJob(
            id="job_x",
            created_at=_NOW,
            raw_request="x",
            detected_intent="code_task",
            autonomy_mode="draft",
            status="draft",
            bogus_field="nope",
        )


def test_no_record_uses_pak_suffix():
    """Pak-suffix guard: no Dispatch record name may end with 'Pak'."""

    assert not [n for n in DISPATCH_RECORD_MODELS if n.endswith("Pak")]
    # The guard itself must fire on a violating registry.
    bad = dict(DISPATCH_RECORD_MODELS)
    bad["VaultPak"] = DispatchJob
    with pytest.raises(PakSuffixCollisionError):
        _assert_no_pak_suffix(bad)
