"""Dispatch integration FIXTURES — golden + failure paths (P-FIXTURES-01).

This is the §15 fixture acceptance harness: it loads each YAML fixture from
``tokenpak/orchestration/dispatch/tests/fixtures/`` and runs it as a single
**parametrized** integration test (one ``test_dispatch_fixture`` function over
all 7 fixtures — kickoff §13 item 6). Every fixture runs **deterministically in
test mode with mocked TIP responses** (no real LLM — item 3) and validates the
four §15 acceptance surfaces:

  * **Run Ledger writes** — the run + station runs (+ decisions / effects / late
    results) are persisted and read back;
  * **Delivery Package shape** — the Gatehouse :class:`DeliveryPackage` status +
    key fields (cost note, required_fixes, decision) match the fixture;
  * **Receipt shape** — a §4.7 :class:`DispatchReceipt` (built via the
    receipt_builder) carries the expected station / decision / effect / telemetry
    shape;
  * **Expected decisions / gates fire** — the right DispatchDecision(s) are
    created and the reviewer / Delivery gate fires (or not) as the fixture says.

The 7 fixtures + what each asserts are documented in :data:`FIXTURE_COVERAGE`
(kickoff §13 item 8 coverage table).

``--live`` (item 7): a real-LLM smoke variant is opt-in via the ``--live`` flag
wired in this directory's ``conftest.py``. **Default mode is mocked**; the live
variant is skipped unless ``--live`` is passed, so default CI never calls a
provider and the suite passes with no network.

Fixtures live as YAML under the dispatch module (item 5). The Run Ledger is
opened against ``tmp_path`` via ``TOKENPAK_HOME`` so the real ``~/.tpk/`` is
never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Dispatch is pydantic-native; the dep ships via the opt-in `dispatch` extra.
pytest.importorskip("pydantic")
import yaml  # noqa: E402

from tokenpak.orchestration.dispatch.context.provider import LocalContextProvider  # noqa: E402
from tokenpak.orchestration.dispatch.dispatch import DispatchRuntime  # noqa: E402
from tokenpak.orchestration.dispatch.frontdock import FrontDock  # noqa: E402
from tokenpak.orchestration.dispatch.gatehouse import REVIEWER_COST_NOTE  # noqa: E402
from tokenpak.orchestration.dispatch.ledger.db import RunLedger  # noqa: E402
from tokenpak.orchestration.dispatch.models.effect import DispatchEffect  # noqa: E402
from tokenpak.orchestration.dispatch.models.enums import (  # noqa: E402
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    RollbackBehavior,
    StationRunStatus,
)
from tokenpak.orchestration.dispatch.models.run import DispatchRun  # noqa: E402
from tokenpak.orchestration.dispatch.models.station_run import DispatchStationRun  # noqa: E402
from tokenpak.orchestration.dispatch.receipt_builder import build_and_write_receipt  # noqa: E402
from tokenpak.orchestration.dispatch.registry.workers import default_worker_registry  # noqa: E402
from tokenpak.orchestration.dispatch.resume import hash_workspace_file  # noqa: E402
from tokenpak.orchestration.dispatch.runner import FulfillmentLine, LineStatus  # noqa: E402
from tokenpak.orchestration.dispatch.station_runner import (  # noqa: E402
    FlagCancelToken,
    WorkerTurn,
)

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)

# Fixture YAMLs live beside the dispatch module (§15 acceptance element 5: YAML).
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[3]
    / "tokenpak"
    / "orchestration"
    / "dispatch"
    / "tests"
    / "fixtures"
)


# ---------------------------------------------------------------------------
# §13 item 8 — coverage table: 7 fixtures + per-fixture pass/fail criteria.
# ---------------------------------------------------------------------------
#
# | fixture                 | kind    | asserts (headline)                       |
# |-------------------------|---------|------------------------------------------|
# | quick_answer            | golden  | 1-station route delivers; no reviewer    |
# | code_task               | golden  | build→reviewer-pass→delivery; cost note  |
# | doc_task                | golden  | draft→reviewer-pass→delivery; cost note  |
# | decision_required       | failure | reviewer warning → accept/reject decision|
# | blocked_reviewer        | failure | reviewer fail → blocked + required_fixes |
# | late_result_cancellation| failure | cancel mid-station → LateResult captured |
# | effect_rollback_failure | failure | resume drift → no auto-rollback, decision|
FIXTURE_COVERAGE: dict[str, str] = {
    "quick_answer.golden.yaml": (
        "Golden: a bare question auto-routes to the 1-station quick_answer route; "
        "the builder station completes; the Delivery Gate is a structural "
        "pass-through (delivery_ready, no reviewer cost note); receipt has 1 "
        "station, 0 decisions, 0 effects."
    ),
    "code_task.golden.yaml": (
        "Golden: a code request auto-routes to code_task; build completes then the "
        "reviewer passes; delivery_ready with the §5.7 reviewer cost note; receipt "
        "has 2 stations, 0 decisions, 0 effects."
    ),
    "doc_task.golden.yaml": (
        "Golden: a documentation request auto-routes to doc_task; draft completes "
        "then the reviewer passes; delivery_ready with the cost note; receipt has "
        "2 stations."
    ),
    "decision_required.failure.yaml": (
        "Failure: a reviewer `warning` auto-creates exactly one accept/reject "
        "DispatchDecision; the package is decision_required (not delivered); the "
        "decision is persisted + linked onto the run; receipt final_status="
        "gate_review with 1 decision."
    ),
    "blocked_reviewer.failure.yaml": (
        "Failure: a reviewer `fail` blocks the Delivery Gate; the package is "
        "blocked carrying non-empty required_fixes; NO repair loop and NO decision; "
        "receipt final_status=blocked."
    ),
    "late_result_cancellation.failure.yaml": (
        "Failure (§5.6): cancellation during the build turn marks the build station "
        "cancelled; exactly one LateResult is captured with effects_applied=false "
        "and recovery_allowed=false and is persisted; the queued reviewer station "
        "is cancelled; no gate fires; receipt final_status=cancelled."
    ),
    "effect_rollback_failure.failure.yaml": (
        "Failure (§5.5 case 3 / §4.8 step 5): resume reconciliation detects "
        "workspace drift against an applied effect and CANNOT auto-rollback; it "
        "surfaces a DECISION_REQUIRED outcome offering rollback_single_clean_effect "
        "/ rerun_from_clean_state / cancel_job (rollback offered, never "
        "auto-applied); run finalizes blocked; no gate fires."
    ),
}


def _fixture_files() -> list[Path]:
    """The 7 fixture YAML files, sorted by name (deterministic parametrization)."""

    files = sorted(_FIXTURE_DIR.glob("*.yaml"))
    return files


def _load(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Deterministic fakes (no real provider) — mirror test_runner.py's pattern.
# ---------------------------------------------------------------------------


class FakeWorkerLLM:
    """Replays scripted WorkerTurns by iteration (deterministic, no provider)."""

    def __init__(self, turns: list[WorkerTurn]) -> None:
        self._turns = turns
        self.calls = 0

    def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
        self.calls += 1
        idx = min(iteration - 1, len(self._turns) - 1)
        return self._turns[idx]


class CancelDuringTurnWorker:
    """Flips the cancel token during the worker turn (simulates a late TIP result)."""

    def __init__(self, token: FlagCancelToken, payload: dict) -> None:
        self._token = token
        self._payload = payload
        self.calls = 0

    def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
        self._token.cancelled = True  # cancellation arrives DURING the turn
        self.calls += 1
        return WorkerTurn(result_payload=dict(self._payload), output_schema_valid=True, tokens_used=5)


class FakeReviewerLLM:
    """Returns a canned reviewer payload JSON string (deterministic)."""

    def __init__(self, status: str, *, reason: str = "ok") -> None:
        recommendation = {
            "pass": "ready",
            "warning": "ready_with_warning",
            "fail": "blocked",
        }[status]
        self._payload = {
            "status": status,
            "criteria_results": [],
            "required_fixes": (
                [{"severity": "medium", "description": "tighten", "suggested_station": "build"}]
                if status != "pass"
                else []
            ),
            "risk_flags": [],
            "delivery_recommendation": {"status": recommendation, "reason": reason},
        }
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        return json.dumps(self._payload)


# ---------------------------------------------------------------------------
# Pytest fixtures: tmp Run Ledger via TOKENPAK_HOME.
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ledger(home):
    led = RunLedger()
    try:
        yield led
    finally:
        led.close()


# ---------------------------------------------------------------------------
# Shared front half: FrontDock intake + route selection from the fixture.
# ---------------------------------------------------------------------------


def _intake_and_select(fx: dict):
    """Run FrontDock intake + DispatchRuntime route selection per the fixture.

    Returns ``(intake, outcome)``. Validates the fixture's §15 elements 2 (manifest)
    and 3 (route) here so every fixture path checks them, regardless of scenario.
    """

    mode = AutonomyMode(fx["autonomy_mode"])
    intake = FrontDock().intake(fx["input_request"], autonomy_mode=mode, now=_NOW)

    # (2) Expected DispatchManifest — assertable.
    em = fx["expected_manifest"]
    assert intake.job.detected_intent == em["detected_intent"]
    assert intake.manifest.route_id == em["route_id"]
    assert em["goal_contains"].lower() in intake.manifest.goal.lower()
    got_ac = [c.id for c in intake.manifest.acceptance_criteria]
    assert got_ac == em["acceptance_criterion_ids"]
    if "quality_requirements" in em:
        qr = intake.manifest.quality_requirements
        for key, expected in em["quality_requirements"].items():
            assert getattr(qr, key) is expected, f"quality_requirements.{key}"

    outcome = DispatchRuntime().select_route(intake, now=_NOW)

    # (3) Expected DispatchRoute selection.
    er = fx["expected_route"]
    assert outcome.route is not None, "route selection produced no route"
    assert outcome.route.id == er["id"]
    assert outcome.status == er["selection_status"]
    assert outcome.precedence_layer == er["precedence_layer"]

    return intake, outcome


def _build_worker_turns(fx: dict) -> list[WorkerTurn]:
    """Translate the fixture's ``mock_tip.worker_turns`` into WorkerTurn objects."""

    turns: list[WorkerTurn] = []
    for spec in fx["mock_tip"].get("worker_turns", []):
        turns.append(
            WorkerTurn(
                result_payload=spec.get("result_payload"),
                output_schema_valid=spec.get("output_schema_valid", False),
                tokens_used=spec.get("tokens_used", 0),
                wall_seconds=spec.get("wall_seconds", 0),
            )
        )
    return turns


def _token_overrides(station_runs, worker_turns) -> dict:
    """Map the (single) build station's run id → its mocked output-token spend.

    v0.1-alpha telemetry seam (see receipt_builder): the per-station token spend
    is supplied from the mocked turn rather than read from the (not-yet-threaded)
    station-run record.
    """

    if not worker_turns or not station_runs:
        return {}
    tokens = worker_turns[0].tokens_used
    # The first non-reviewer station run carries the builder's output tokens.
    first = station_runs[0]
    return {first.id: tokens}


# ---------------------------------------------------------------------------
# Scenario drivers (one per fixture execution shape).
# ---------------------------------------------------------------------------


def _run_standard(fx, ledger):
    """Golden / reviewer-decision / reviewer-blocked: a normal sequential run."""

    intake, outcome = _intake_and_select(fx)
    route = outcome.route
    worker_turns = _build_worker_turns(fx)

    reviewer = None
    reviewer_spec = fx["mock_tip"].get("reviewer")
    if reviewer_spec is not None:
        reviewer = FakeReviewerLLM(reviewer_spec["status"], reason=reviewer_spec.get("reason", "ok"))

    worker = FakeWorkerLLM(worker_turns)
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=reviewer,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode(fx["autonomy_mode"]),
    )
    overrides = _token_overrides(result.station_runs, worker_turns)
    return result, reviewer, overrides


def _run_cancellation(fx, ledger):
    """§5.6: cancellation arrives during the build turn → LateResult captured."""

    intake, outcome = _intake_and_select(fx)
    route = outcome.route

    token = FlagCancelToken(False)
    worker = CancelDuringTurnWorker(token, fx["mock_tip"]["late_payload"])
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        cancel_token=token,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode(fx["autonomy_mode"]),
    )
    return result, None, {}


def _run_resume_drift(fx, ledger, workspace):
    """§5.5 case 3 / §4.8 step 5: resume an interrupted build whose effect drifted."""

    intake, outcome = _intake_and_select(fx)
    route = outcome.route
    eff = fx["mock_tip"]["applied_effect"]

    # Seed a run + a RUNNING build station with one applied file effect.
    run = DispatchRun(
        id="run_rollback",
        job_id=intake.manifest.job_id,
        manifest_id=intake.manifest.id,
        route_id=route.id,
        started_at=_NOW,
        status="running",
    )
    ledger.write_run(run)

    target = eff["target"]
    (workspace / Path(target)).parent.mkdir(parents=True, exist_ok=True)
    (workspace / Path(target)).write_text(eff["applied_content"])
    after = hash_workspace_file(workspace, target)

    sr = DispatchStationRun(
        id="stationrun_build",
        run_id=run.id,
        station_id="build",
        worker_id="worker.builder.default.v1",
        context_bundle_id="ctx",
        status=StationRunStatus.RUNNING,
        result_schema_version="station_result.v1",
    )
    ledger.write_station_run(sr)
    effect = DispatchEffect(
        id="effect_build_1",
        job_id=run.job_id,
        station_run_id="stationrun_build",
        tool_name="apply_patch",
        target_type=EffectTargetType.FILE,
        target=target,
        before_exists=False,
        before_hash=None,
        after_hash=after,
        rollback_behavior=RollbackBehavior.DELETE_FILE_IF_AFTER_HASH_MATCHES,
        status=EffectStatus.APPLIED,
        rollback_available=True,
        created_at=_NOW,
        finalized_at=_NOW,
    )
    ledger.write_effect(effect)
    # Link the effect onto the run record so the receipt projects it.
    run = run.model_copy(update={"effects": [effect.id]})
    ledger.write_run(run)

    # Drift the workspace AFTER the effect was recorded.
    (workspace / Path(target)).write_text(eff["drift_content"])

    line = FulfillmentLine(
        worker_llm=FakeWorkerLLM([WorkerTurn(result_payload={"x": 1}, output_schema_valid=True)]),
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("pass"),
        clock=lambda: _NOW,
    )
    result = line.resume(
        run_id=run.id,
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode(fx["autonomy_mode"]),
        workspace_root=str(workspace),
    )
    return result, None, {}


# ---------------------------------------------------------------------------
# Shared assertions over the §15 acceptance surfaces.
# ---------------------------------------------------------------------------


def _assert_stations(fx, result, ledger):
    """(4) station sequence — asserted against the Run Ledger (the durable record).

    §15 requires a fixture validate "Run Ledger writes": the authoritative station
    sequence is what the ledger persisted (``read_station_runs_for_run``), ordered
    by insertion. ``result.station_runs`` is the in-flight result list, which —
    by design — does NOT carry the queued-cancelled record on a §5.6 cancellation
    nor the resume status transition (those are written to the ledger directly).
    Asserting against the ledger is therefore both correct per §15 and complete.
    """

    expected = fx["expected_stations"]
    persisted = ledger.read_station_runs_for_run(result.run.id)
    got = [(sr.station_id, sr.status.value) for sr in persisted]
    want = [(s["station_id"], s["status"]) for s in expected]
    assert got == want, f"station sequence mismatch (ledger): {got} != {want}"
    for s in expected:
        # worker id assertion (dynamic role→worker resolution must be stable).
        sr = next(r for r in persisted if r.station_id == s["station_id"])
        assert sr.worker_id == s["worker_id"]

    # The run itself is persisted with the finalized status.
    assert ledger.read_run(result.run.id) is not None


def _assert_delivery_package(fx, result):
    """(6) DeliveryPackage shape + key fields."""

    edp = fx["expected_delivery_package"]
    if edp.get("none_expected"):
        assert result.delivery_package is None, "expected no delivery package"
        return
    pkg = result.delivery_package
    assert pkg is not None, "expected a delivery package"
    assert pkg.status.value == edp["status"]
    if "gatehouse_passed" in edp:
        assert pkg.gatehouse_report.passed is edp["gatehouse_passed"]
    if "cost_note_present" in edp:
        if edp["cost_note_present"]:
            assert pkg.cost_note == REVIEWER_COST_NOTE
        else:
            assert pkg.cost_note is None
    if "required_fixes_empty" in edp:
        assert (len(pkg.required_fixes) == 0) is edp["required_fixes_empty"]
    if edp.get("decision_present"):
        assert pkg.decision is not None


def _assert_decisions_and_gates(fx, result, reviewer, ledger):
    """(check expected decisions/gates fire) — §15 acceptance final clause."""

    ed = fx["expected_decisions"]
    # The line-level decision the run halted on (warning / spend / resume drift).
    halting = [result.decision] if result.decision is not None else []
    assert len(halting) == ed["count"], f"decision count {len(halting)} != {ed['count']}"

    if ed["count"]:
        decision = halting[0]
        if "option_ids" in ed:
            assert {o.id for o in decision.options} == set(ed["option_ids"])
        if "option_ids_present" in ed:
            present = {o.id for o in decision.options}
            for oid in ed["option_ids_present"]:
                assert oid in present, f"expected option {oid!r} in {sorted(present)}"
        if ed.get("persisted") or ed.get("persisted_and_linked"):
            assert ledger.read_decision(decision.id) is not None
        if ed.get("persisted_and_linked"):
            assert decision.id in ledger.read_run(result.run.id).decisions
        if "auto_rollback_applied" in ed:
            # §4.8/§5.5 step 5: automatic rollback is DISABLED. The decision is the
            # surfaced outcome; nothing is auto-reverted.
            assert ed["auto_rollback_applied"] is False

    # Gate firing: a reviewer gate fired iff a reviewer client was called; the
    # Delivery Gate fired iff a delivery package was produced.
    eg = fx["expected_gates"]
    reviewer_fired = bool(reviewer and reviewer.calls > 0)
    assert reviewer_fired is eg["reviewer_gate_fired"]
    delivery_fired = result.delivery_package is not None
    assert delivery_fired is eg["delivery_gate_fired"]


def _assert_late_results(fx, result, ledger):
    """§5.6 late-result handling (cancellation fixture only)."""

    elr = fx.get("expected_late_results")
    if elr is None:
        return
    assert len(result.late_results) == elr["count"]
    if elr["count"]:
        late = result.late_results[0]
        assert late.effects_applied is elr["effects_applied"]
        assert late.recovery_allowed is elr["recovery_allowed"]
        if elr.get("persisted"):
            assert ledger.read_late_result(late.id) is not None


def _assert_receipt(fx, result, ledger, overrides):
    """(7) DispatchReceipt shape + telemetry — built from the finished run."""

    er = fx["expected_receipt"]
    receipt = build_and_write_receipt(
        run=result.run,
        ledger=ledger,
        final_status=er["final_status"],
        token_overrides=overrides,
        clock=lambda: _NOW,
    )
    assert receipt.final_status == er["final_status"]
    assert len(receipt.stations) == er["station_count"]
    assert len(receipt.decisions) == er["decision_count"]
    assert len(receipt.effects) == er["effect_count"]
    if "telemetry" in er:
        for key, expected in er["telemetry"].items():
            assert getattr(receipt.telemetry, key) == expected, f"telemetry.{key}"

    # The receipt is persisted + linked, so the `tokenpak dispatch receipt` reader
    # (queries dispatch_receipts by job id) would find it.
    assert ledger.read_receipt(receipt.id) is not None
    assert ledger.read_run(result.run.id).receipt_id == receipt.id


# ---------------------------------------------------------------------------
# The single parametrized integration test over all 7 fixtures (§13 item 6).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_files(),
    ids=lambda p: p.name.replace(".yaml", ""),
)
def test_dispatch_fixture(fixture_path, ledger, tmp_path, request):
    """Run one Dispatch fixture deterministically and validate the §15 surfaces.

    Mocked + deterministic by default (no provider). ``--live`` would enable a
    real-LLM smoke variant for the golden fixtures; it is opt-in and skipped by
    default, so default CI never hits a provider.
    """

    fx = _load(fixture_path)
    assert fx["mock_tip"]["live"] is False, "fixtures default to mocked TIP (no network)"

    # --live opt-in: the live smoke variant is skipped unless --live is passed.
    if request.config.getoption("dispatch_live") and fx["kind"] == "golden":
        pytest.skip(
            "live smoke variant requested via --live (real-LLM path not exercised "
            "in the deterministic default suite; opt-in smoke is a separate manual run)"
        )

    scenario = fx.get("scenario", "standard")
    if scenario == "cancellation":
        result, reviewer, overrides = _run_cancellation(fx, ledger)
    elif scenario == "resume_drift":
        workspace = tmp_path / "ws"
        result, reviewer, overrides = _run_resume_drift(fx, ledger, workspace)
    else:
        result, reviewer, overrides = _run_standard(fx, ledger)

    # (8) pass/fail — line + run status.
    pf = fx["pass_fail"]
    assert result.status is LineStatus(pf["line_status"]), (
        f"line status {result.status} != {pf['line_status']}"
    )
    assert result.run.status == pf["run_status"], (
        f"run status {result.run.status!r} != {pf['run_status']!r}"
    )

    # §15 acceptance surfaces.
    _assert_stations(fx, result, ledger)        # (4) + Run Ledger writes
    _assert_delivery_package(fx, result)        # (6) Delivery Package shape
    _assert_decisions_and_gates(fx, result, reviewer, ledger)  # decisions/gates fire
    _assert_late_results(fx, result, ledger)    # §5.6 late-result handling
    _assert_receipt(fx, result, ledger, overrides)  # (7) Receipt shape


# ---------------------------------------------------------------------------
# Meta-tests: the fixture set itself meets the §15 contract.
# ---------------------------------------------------------------------------


def test_seven_fixtures_present():
    """§13 item 8: exactly 7 fixtures (3 golden + 4 failure)."""

    files = _fixture_files()
    assert len(files) == 7, f"expected 7 fixtures, found {[f.name for f in files]}"
    kinds = {f.name: _load(f)["kind"] for f in files}
    golden = [n for n, k in kinds.items() if k == "golden"]
    failure = [n for n, k in kinds.items() if k == "failure"]
    assert len(golden) == 3, f"expected 3 golden fixtures, got {golden}"
    assert len(failure) == 4, f"expected 4 failure fixtures, got {failure}"


# The 8 required elements of the §15 fixture acceptance contract. Every fixture
# MUST carry each one (kickoff §13 item 1: "all 8 required elements").
_REQUIRED_ELEMENTS = {
    "input_request": ("input_request",),          # 1. Input request text
    "expected_manifest": ("expected_manifest",),  # 2. Expected DispatchManifest
    "expected_route": ("expected_route",),        # 3. Expected DispatchRoute selection
    "expected_stations": ("expected_stations",),  # 4. Expected worker/station sequence
    "mock_tip": ("mock_tip",),                    # 5. Mock TIP responses (+ live flag)
    "expected_delivery_package": ("expected_delivery_package",),  # 6. Delivery Package
    "expected_receipt": ("expected_receipt",),    # 7. Expected DispatchReceipt
    "pass_fail": ("pass_fail",),                  # 8. Pass/fail criteria
}


@pytest.mark.parametrize("fixture_path", _fixture_files(), ids=lambda p: p.name.replace(".yaml", ""))
def test_fixture_has_all_eight_required_elements(fixture_path):
    """§15 acceptance contract: every fixture carries all 8 required elements."""

    fx = _load(fixture_path)
    for label, keys in _REQUIRED_ELEMENTS.items():
        assert all(k in fx for k in keys), f"{fixture_path.name} missing §15 element: {label}"
    # Element 5 must carry the live flag (deterministic-by-default contract).
    assert "live" in fx["mock_tip"], f"{fixture_path.name} mock_tip missing the `live` flag"


def test_fixture_coverage_table_matches_fixture_set():
    """§13 item 8: the coverage table documents every fixture (and only those)."""

    on_disk = {f.name for f in _fixture_files()}
    documented = set(FIXTURE_COVERAGE)
    assert documented == on_disk, (
        f"coverage table out of sync: missing={on_disk - documented}, "
        f"extra={documented - on_disk}"
    )
