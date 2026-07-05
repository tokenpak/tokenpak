"""Tests for the Dispatch Run Ledger (P-LEDGER-01).

Covers Standards Delta v0 §4 record persistence + §4.8/§5.5 effect lifecycle:
  * write/read round-trip for each of the ten record tables;
  * transaction rollback on error leaves no partial row;
  * schema migration v0 -> v1 (versioned, idempotent);
  * DispatchEffect planned -> applied lifecycle + dangling-planned query.

The ledger is path-resolved via ``tokenpak._paths.under('dispatch')``; every
test points ``TOKENPAK_HOME`` at ``tmp_path`` so nothing touches the real
``~/.tpk/``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Dispatch is pydantic-native; the dep ships via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.ledger import (  # noqa: E402
    SCHEMA_VERSION,
    RunLedger,
    ledger_db_path,
)
from tokenpak.orchestration.dispatch.ledger.migrations import (  # noqa: E402
    get_current_schema_version,
    migrate,
)
from tokenpak.orchestration.dispatch.models import (  # noqa: E402
    DispatchArtifact,
    DispatchDecision,
    DispatchEffect,
    DispatchJob,
    DispatchManifest,
    DispatchReceipt,
    DispatchRoute,
    DispatchRun,
    DispatchStationRun,
    LateResult,
)

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point TOKENPAK_HOME at a tmp dir so the ledger never touches ~/.tpk/."""

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ledger(home):
    """A RunLedger opened at the canonical (tmp-rooted) path."""

    led = RunLedger()
    try:
        yield led
    finally:
        led.close()


# ---------------------------------------------------------------------------
# Record builders (one minimal-valid instance per record class)
# ---------------------------------------------------------------------------


def _job(job_id="job_01"):
    return DispatchJob(
        id=job_id,
        created_at=_NOW,
        raw_request="add a CLI flag",
        detected_intent="code_task",
        autonomy_mode="dispatch_with_approval",
        status="running",
    )


def _manifest(manifest_id="manifest_01", job_id="job_01"):
    return DispatchManifest(
        id=manifest_id,
        job_id=job_id,
        route_id="route.code_task.v1",
        goal="add a --json flag",
        permissions={"autonomy_mode": "dispatch_with_approval"},
        quality_requirements={
            "test_required": True,
            "review_required": True,
            "docs_required": False,
            "evidence_required": False,
        },
        status="active",
    )


def _route(route_id="route.code_task.v1"):
    return DispatchRoute(
        id=route_id,
        name="code_task",
        description="standard code task route",
        default_risk="medium",
    )


def _run(run_id="run_01", job_id="job_01"):
    return DispatchRun(
        id=run_id,
        job_id=job_id,
        manifest_id="manifest_01",
        route_id="route.code_task.v1",
        started_at=_NOW,
        status="running",
    )


def _station_run(station_run_id="stationrun_01", run_id="run_01"):
    return DispatchStationRun(
        id=station_run_id,
        run_id=run_id,
        station_id="build",
        worker_id="worker.code_builder.v1",
        context_bundle_id="ctx_01",
        status="running",
        result_schema_version="station_result.v1",
    )


def _decision(decision_id="decision_01", job_id="job_01"):
    return DispatchDecision(
        id=decision_id,
        job_id=job_id,
        created_at=_NOW,
        scope="job",
        title="pick an option",
        question="which approach?",
        reason="ambiguous request",
        risk_level="medium",
        options=[{"id": "a", "label": "A", "description": "do A"}],
        recommendation={"option_id": "a", "rationale": "simplest"},
        default_action={"option_id": "a"},
        status="pending",
    )


def _artifact(artifact_id="artifact_01", job_id="job_01"):
    return DispatchArtifact(
        id=artifact_id,
        job_id=job_id,
        kind="patch",
        target="artifacts/patch_01.diff",
        content_hash="sha256:abc",
        created_at=_NOW,
    )


def _receipt(receipt_id="receipt_01", job_id="job_01", run_id="run_01"):
    return DispatchReceipt(
        id=receipt_id,
        job_id=job_id,
        run_id=run_id,
        route_id="route.code_task.v1",
        final_status="delivered",
        created_at=_NOW,
    )


def _late_result(late_id="late_01", job_id="job_01"):
    return LateResult(
        id=late_id,
        job_id=job_id,
        station_run_id="stationrun_01",
        received_at=_NOW,
        result_hash="sha256:def",
    )


def _planned_effect(effect_id="effect_01", station_run_id="stationrun_01"):
    """A 'create' DispatchEffect in the planned state (no finalized_at)."""

    return DispatchEffect(
        id=effect_id,
        job_id="job_01",
        station_run_id=station_run_id,
        tool_name="apply_patch",
        target_type="file",
        target="src/foo.py",
        before_exists=False,
        after_hash="sha256:after",
        rollback_behavior="delete_file_if_after_hash_matches",
        status="planned",
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Path resolution / DB location (criterion 1 + 6)
# ---------------------------------------------------------------------------


def test_ledger_db_path_under_tokenpak_home(home):
    """runs.db resolves under <TOKENPAK_HOME>/dispatch/, never the project repo."""

    path = ledger_db_path()
    assert path == home / "dispatch" / "runs.db"
    assert path.name == "runs.db"
    assert "tokenpak-dev" not in str(path)  # never the project repo


def test_open_creates_db_and_parent(home):
    """Opening the ledger creates the dispatch/ dir and the DB file on disk."""

    led = RunLedger()
    try:
        assert (home / "dispatch").is_dir()
        assert (home / "dispatch" / "runs.db").exists()
    finally:
        led.close()


# ---------------------------------------------------------------------------
# Schema migration v0 -> v1 (criterion 3 + 9)
# ---------------------------------------------------------------------------


def test_fresh_ledger_is_at_current_schema_version(ledger):
    assert ledger.schema_version == SCHEMA_VERSION == 2


def test_migration_creates_all_ten_tables(ledger):
    expected = {
        "dispatch_jobs",
        "dispatch_manifests",
        "dispatch_routes",
        "dispatch_runs",
        "dispatch_station_runs",
        "dispatch_decisions",
        "dispatch_artifacts",
        "dispatch_receipts",
        "dispatch_effects",
        "late_results",
    }
    rows = ledger._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert expected <= names


def test_migration_is_idempotent(ledger):
    """A second migrate() on an already-current DB is a no-op (criterion 3)."""

    before = get_current_schema_version(ledger._conn)
    result = migrate(ledger._conn)  # re-run
    after = get_current_schema_version(ledger._conn)
    assert before == after == SCHEMA_VERSION == result


def test_migration_from_empty_v0(home):
    """A bare v0 (user_version=0) DB migrates cleanly up to v1."""

    import sqlite3

    db = home / "dispatch" / "runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        assert get_current_schema_version(conn) == 0
        migrate(conn)
        assert get_current_schema_version(conn) == SCHEMA_VERSION
        # tables now exist (v1 record tables + the v2 lease sidecar)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('dispatch_runs', 'dispatch_run_leases')"
        ).fetchall()
        assert len(rows) == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Write/read round-trip for every record table (criterion 2 + 8)
# ---------------------------------------------------------------------------


def test_roundtrip_job(ledger):
    ledger.write_job(_job())
    assert ledger.read_job("job_01") == _job()


def test_roundtrip_manifest(ledger):
    ledger.write_manifest(_manifest())
    got = ledger.read_manifest("manifest_01")
    assert got == _manifest()


def test_roundtrip_route(ledger):
    ledger.write_route(_route())
    assert ledger.read_route("route.code_task.v1") == _route()


def test_roundtrip_run(ledger):
    ledger.write_run(_run())
    assert ledger.read_run("run_01") == _run()


def test_roundtrip_station_run(ledger):
    ledger.write_station_run(_station_run())
    assert ledger.read_station_run("stationrun_01") == _station_run()


def test_roundtrip_decision(ledger):
    ledger.write_decision(_decision())
    assert ledger.read_decision("decision_01") == _decision()


def test_roundtrip_artifact(ledger):
    ledger.write_artifact(_artifact())
    assert ledger.read_artifact("artifact_01") == _artifact()


def test_roundtrip_receipt(ledger):
    ledger.write_receipt(_receipt())
    assert ledger.read_receipt("receipt_01") == _receipt()


def test_roundtrip_late_result(ledger):
    ledger.write_late_result(_late_result())
    assert ledger.read_late_result("late_01") == _late_result()


def test_roundtrip_effect(ledger):
    ledger.write_effect(_planned_effect())
    assert ledger.read_effect("effect_01") == _planned_effect()


def test_read_missing_returns_none(ledger):
    assert ledger.read_job("nope") is None
    assert ledger.read_effect("nope") is None


def test_indexed_columns_are_persisted(ledger):
    """Identity/index columns are written alongside the payload (criterion 8)."""

    ledger.write_run(_run())
    row = ledger._conn.execute(
        "SELECT job_id, status, manifest_id FROM dispatch_runs WHERE id=?",
        ("run_01",),
    ).fetchone()
    assert row["job_id"] == "job_01"
    assert row["status"] == "running"
    assert row["manifest_id"] == "manifest_01"


def test_write_or_replace_overwrites(ledger):
    """Re-writing the same id replaces (no duplicate / no error)."""

    ledger.write_job(_job())
    updated = _job()
    updated.detected_intent = "doc_task"
    ledger.write_job(updated)
    got = ledger.read_job("job_01")
    assert got.detected_intent == "doc_task"
    count = ledger._conn.execute(
        "SELECT COUNT(*) AS c FROM dispatch_jobs WHERE id=?", ("job_01",)
    ).fetchone()["c"]
    assert count == 1


# ---------------------------------------------------------------------------
# Atomic writes / transaction rollback (criterion 4)
# ---------------------------------------------------------------------------


def test_rollback_on_error_leaves_no_partial_row(ledger):
    """A failed write rolls back; no partial row is visible (criterion 4)."""

    # Force a failure inside the transaction by passing a column that does not
    # exist; the INSERT must raise and roll back, leaving the table empty.
    with pytest.raises(Exception):
        ledger._insert(
            "dispatch_jobs",
            {"id": "job_bad", "no_such_column": "x", "payload": "{}"},
        )
    rows = ledger._conn.execute(
        "SELECT COUNT(*) AS c FROM dispatch_jobs"
    ).fetchone()
    assert rows["c"] == 0


def test_successful_write_is_committed(ledger):
    """A committed write survives a fresh connection to the same file."""

    ledger.write_job(_job())
    ledger.close()
    reopened = RunLedger()
    try:
        assert reopened.read_job("job_01") == _job()
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# DispatchEffect lifecycle (criterion 5 — Standards Delta v0 §4.8 / §5.5)
# ---------------------------------------------------------------------------


def test_record_planned_effect_writes_planned_no_finalized(ledger):
    ledger.record_planned_effect(_planned_effect())
    got = ledger.read_effect("effect_01")
    assert got.status.value == "planned"
    assert got.finalized_at is None


def test_record_planned_effect_rejects_non_planned(ledger):
    eff = _planned_effect()
    eff.status = eff.status.__class__.APPLIED
    with pytest.raises(ValueError):
        ledger.record_planned_effect(eff)


def test_planned_to_applied_lifecycle(ledger):
    ledger.record_planned_effect(_planned_effect())
    applied = ledger.mark_effect_applied(
        "effect_01", finalized_at=_NOW, rollback_available=True
    )
    assert applied.status.value == "applied"
    assert applied.finalized_at == _NOW
    assert applied.rollback_available is True
    # persisted
    got = ledger.read_effect("effect_01")
    assert got.status.value == "applied"
    assert got.finalized_at == _NOW


def test_planned_to_failed_lifecycle(ledger):
    ledger.record_planned_effect(_planned_effect())
    failed = ledger.mark_effect_failed("effect_01", finalized_at=_NOW)
    assert failed.status.value == "failed"
    assert failed.finalized_at == _NOW
    got = ledger.read_effect("effect_01")
    assert got.status.value == "failed"


def test_mark_effect_applied_defaults_finalized_at(ledger):
    ledger.record_planned_effect(_planned_effect())
    applied = ledger.mark_effect_applied("effect_01")
    assert applied.finalized_at is not None


def test_finalize_unknown_effect_raises(ledger):
    with pytest.raises(KeyError):
        ledger.mark_effect_applied("missing")


def test_select_dangling_planned_effects(ledger):
    """Resume reconciliation reads planned effects with no finalized_at (§5.5)."""

    # A run with two station runs; one effect stays planned (dangling), one is
    # applied, one belongs to a different run.
    ledger.write_run(_run("run_01"))
    ledger.write_station_run(_station_run("stationrun_01", "run_01"))
    ledger.write_station_run(_station_run("stationrun_02", "run_01"))
    ledger.write_run(_run("run_99", "job_99"))
    ledger.write_station_run(_station_run("stationrun_99", "run_99"))

    # dangling planned (run_01)
    ledger.record_planned_effect(
        _planned_effect("effect_dangling", "stationrun_01")
    )
    # applied (run_01) — must NOT be returned
    ledger.record_planned_effect(_planned_effect("effect_done", "stationrun_02"))
    ledger.mark_effect_applied("effect_done", finalized_at=_NOW)
    # dangling planned but on a different run — must NOT be returned
    ledger.record_planned_effect(_planned_effect("effect_other", "stationrun_99"))

    dangling = ledger.select_dangling_planned_effects("run_01")
    ids = {e.id for e in dangling}
    assert ids == {"effect_dangling"}
    assert all(e.status.value == "planned" for e in dangling)
    assert all(e.finalized_at is None for e in dangling)


def test_select_dangling_planned_empty_when_none(ledger):
    ledger.write_run(_run("run_01"))
    ledger.write_station_run(_station_run("stationrun_01", "run_01"))
    ledger.record_planned_effect(_planned_effect("effect_01", "stationrun_01"))
    ledger.mark_effect_applied("effect_01", finalized_at=_NOW)
    assert ledger.select_dangling_planned_effects("run_01") == []


# ---------------------------------------------------------------------------
# Ledger does not promote to canonical Pak types (criterion 7)
# ---------------------------------------------------------------------------


def test_ledger_exposes_no_pak_promotion(ledger):
    """The ledger stores execution records only — no Pak-promotion surface."""

    public = [m for m in dir(ledger) if not m.startswith("_")]
    assert not any("pak" in m.lower() for m in public)
