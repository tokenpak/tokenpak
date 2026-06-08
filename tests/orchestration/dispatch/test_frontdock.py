"""Tests for the FrontDock intake module (Standards Delta v0 §13 item 3).

Verifies, with a FAKE injected TIP client (no real LLM):

  * deterministic-only intent detection makes NO LLM call (mock not invoked);
  * the LLM fallback path fires only on ambiguous input and returns the mocked
    intent;
  * assumption drafting + missing-info detection;
  * a high-risk missing-info gap creates a BLOCKING DispatchDecision (the Front
    Dock never silently assumes for those);
  * the Front Dock Rule — low/medium-value gaps do NOT trigger a blocking
    decision (they become assumptions);
  * every produced record is a schema-valid Pydantic model.
"""

from __future__ import annotations

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from pydantic import BaseModel

from tokenpak.orchestration.dispatch.frontdock import (
    INTENT_CODE_TASK,
    INTENT_DOC_TASK,
    INTENT_QUICK_ANSWER,
    INTENT_TO_ROUTE_HINT,
    INTENT_UNKNOWN,
    RISK_FLAG_REGISTRY,
    FrontDock,
    FrontDockResult,
    UnknownRiskFlagError,
    detect_intent_deterministic,
    detect_risk_flags,
    is_registered_risk_flag,
    risk_flag_level,
)
from tokenpak.orchestration.dispatch.models.decision import DispatchDecision
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    DecisionStatus,
    DispatchJobStatus,
    ManifestStatus,
    RiskLevel,
)
from tokenpak.orchestration.dispatch.models.job import DispatchJob
from tokenpak.orchestration.dispatch.models.manifest import DispatchManifest

# ---------------------------------------------------------------------------
# Fake injected TIP client
# ---------------------------------------------------------------------------


class _FakeTipClient:
    """Records call count + returns a canned intent. NOT a real provider."""

    def __init__(self, intent: str = INTENT_QUICK_ANSWER):
        self._intent = intent
        self.classify_calls = 0
        self.last_request = None
        self.last_candidates = None

    def classify_intent(self, request: str, candidates: list[str]) -> str:
        self.classify_calls += 1
        self.last_request = request
        self.last_candidates = candidates
        return self._intent

    # `complete` intentionally omitted: v0.1-alpha intake never calls it.


# ---------------------------------------------------------------------------
# Schema-validity helper
# ---------------------------------------------------------------------------


def _assert_schema_valid(result: FrontDockResult) -> None:
    """Every produced record must be a schema-valid pydantic model."""

    assert isinstance(result.job, DispatchJob)
    assert isinstance(result.manifest, DispatchManifest)
    assert isinstance(result.job, BaseModel)
    assert isinstance(result.manifest, BaseModel)
    # Round-trip through validation: model_validate(model_dump()) must succeed.
    DispatchJob.model_validate(result.job.model_dump())
    DispatchManifest.model_validate(result.manifest.model_dump())
    if result.decision is not None:
        assert isinstance(result.decision, DispatchDecision)
        DispatchDecision.model_validate(result.decision.model_dump())


# ---------------------------------------------------------------------------
# Deterministic-only intent detection — NO LLM invoked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text,expected_intent",
    [
        ("Implement a function to parse the config file", INTENT_CODE_TASK),
        ("Fix the bug in the login endpoint", INTENT_CODE_TASK),
        ("Write documentation for the onboarding guide", INTENT_DOC_TASK),
        ("Update the README and the changelog", INTENT_DOC_TASK),
        ("What is the difference between a list and a tuple?", INTENT_QUICK_ANSWER),
        ("Explain how the cache works", INTENT_QUICK_ANSWER),
    ],
)
def test_deterministic_intent_no_llm_call(request_text, expected_intent):
    client = _FakeTipClient(intent="should_not_be_used")
    dock = FrontDock(tip_client=client)

    result = dock.intake(request_text)

    assert result.job.detected_intent == expected_intent
    assert result.intent_resolution.source == "deterministic"
    # The deterministic path must NOT consult the LLM.
    assert client.classify_calls == 0
    assert result.job.route_hint == INTENT_TO_ROUTE_HINT[expected_intent]
    _assert_schema_valid(result)


def test_deterministic_precedence_code_over_doc():
    # "fix the bug AND document it" → code_task wins by precedence, no LLM.
    client = _FakeTipClient()
    dock = FrontDock(tip_client=client)
    result = dock.intake("Fix the bug and document the change")
    assert result.job.detected_intent == INTENT_CODE_TASK
    assert result.intent_resolution.source == "deterministic"
    assert client.classify_calls == 0


def test_detect_intent_deterministic_returns_none_on_ambiguous():
    # No keyword signal at all → ambiguous → None (escalation point).
    assert detect_intent_deterministic("zzz qqq foobar baz") is None


def test_no_llm_call_even_with_no_client_on_deterministic_input():
    # Deterministic input resolves with NO client at all.
    dock = FrontDock(tip_client=None)
    result = dock.intake("Implement the feature")
    assert result.job.detected_intent == INTENT_CODE_TASK
    assert result.intent_resolution.source == "deterministic"


# ---------------------------------------------------------------------------
# LLM fallback path — ambiguous input → mock returns intent
# ---------------------------------------------------------------------------


def test_llm_fallback_on_ambiguous_input():
    client = _FakeTipClient(intent=INTENT_QUICK_ANSWER)
    dock = FrontDock(tip_client=client)

    result = dock.intake("zzz qqq foobar baz")  # no deterministic signal

    # The injected TIP client was consulted exactly once.
    assert client.classify_calls == 1
    assert result.intent_resolution.source == "llm"
    assert result.job.detected_intent == INTENT_QUICK_ANSWER
    _assert_schema_valid(result)


def test_llm_fallback_out_of_vocab_answer_falls_to_unknown():
    client = _FakeTipClient(intent="totally_made_up_intent")
    dock = FrontDock(tip_client=client)

    result = dock.intake("zzz qqq foobar baz")

    assert client.classify_calls == 1
    # Out-of-vocabulary LLM answer is not invented into the job.
    assert result.job.detected_intent == INTENT_UNKNOWN
    assert result.intent_resolution.source == "llm"


def test_ambiguous_with_no_client_resolves_unknown_without_provider_call():
    dock = FrontDock(tip_client=None)
    result = dock.intake("zzz qqq foobar baz")
    assert result.job.detected_intent == INTENT_UNKNOWN
    assert result.intent_resolution.source == "unknown"
    # route_hint for unknown is None; manifest records the pre-routing sentinel.
    assert result.job.route_hint is None
    assert result.manifest.route_id == "route.unresolved.v0"


# ---------------------------------------------------------------------------
# Assumption drafting
# ---------------------------------------------------------------------------


def test_assumption_drafting_code_task():
    dock = FrontDock(tip_client=None)
    result = dock.intake("Implement the parser")
    assumptions = result.job.assumptions
    assert assumptions, "code_task should draft starter assumptions"
    # Default code_task assumptions are present.
    assert any("current repository" in a for a in assumptions)
    assert any("external side effects" in a for a in assumptions)
    # Non-material intent probes are downgraded to assumptions (Front Dock Rule).
    assert any("sensible default" in a for a in assumptions)


def test_assumption_drafting_doc_task():
    dock = FrontDock(tip_client=None)
    result = dock.intake("Write the user guide")
    assert any("Markdown document" in a for a in result.job.assumptions)


# ---------------------------------------------------------------------------
# Decision-Card creation triggers — high-risk missing_info → blocking decision
# ---------------------------------------------------------------------------


def test_high_risk_missing_info_creates_blocking_decision():
    dock = FrontDock(tip_client=None)
    # "secret" → touches_secrets (CRITICAL) — a material high-risk surface.
    result = dock.intake("Rotate the API secret in the deploy pipeline")

    assert result.is_blocked
    assert result.decision is not None
    decision = result.decision
    assert isinstance(decision, DispatchDecision)
    assert decision.status is DecisionStatus.PENDING
    # Never silently assumes for high-risk gaps: never auto-applies.
    assert decision.default_action.auto_apply_after.value == "never"
    # Severity reflects the most severe gap (secret = critical).
    assert decision.risk_level is RiskLevel.CRITICAL
    # The blocking gap is recorded in missing_info, not silently assumed away.
    assert any("touches_secrets" in m for m in result.job.missing_info)
    # Manifest reflects the unresolved decision.
    assert result.manifest.status is ManifestStatus.NEEDS_DECISION
    _assert_schema_valid(result)


def test_high_risk_flag_high_level_blocks():
    dock = FrontDock(tip_client=None)
    # "migration" → schema_migration (HIGH).
    result = dock.intake("Run a schema migration that drops the old column")
    assert result.is_blocked
    assert result.decision is not None
    # "delete"/"drop" → deletes_data (HIGH) also present.
    assert "schema_migration" in result.job.risk_flags
    assert result.decision.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


# ---------------------------------------------------------------------------
# Front Dock Rule — low-value missing info does NOT block
# ---------------------------------------------------------------------------


def test_front_dock_rule_low_value_gap_does_not_block():
    dock = FrontDock(tip_client=None)
    # A plain code task: target files / acceptance criteria are "missing" but NOT
    # material → they become assumptions, NOT a blocking decision.
    result = dock.intake("Refactor the parser module")

    assert not result.is_blocked
    assert result.decision is None
    # The gaps are still recorded as missing_info (transparency)...
    assert result.job.missing_info
    # ...but downgraded to assumptions rather than surfaced as blocking questions.
    assert any("sensible default" in a for a in result.job.assumptions)
    # Manifest stays a plain draft (not needs_decision).
    assert result.manifest.status is ManifestStatus.DRAFT
    _assert_schema_valid(result)


def test_front_dock_rule_low_risk_flag_is_assumption_not_block():
    dock = FrontDock(tip_client=None)
    # "docs" → touches_docs (LOW); "cli" → touches_cli (MEDIUM). Neither blocks.
    result = dock.intake("Update the cli docs for the new flag")
    assert not result.is_blocked
    assert result.decision is None
    assert "touches_docs" in result.job.risk_flags
    # Low/medium risk flags are handled as assumptions.
    assert any("low/medium-risk" in a for a in result.job.assumptions)


def test_quick_answer_no_gaps_no_block():
    dock = FrontDock(tip_client=None)
    result = dock.intake("What does the spend guard do?")
    assert result.job.detected_intent == INTENT_QUICK_ANSWER
    assert not result.is_blocked
    assert result.manifest.status is ManifestStatus.DRAFT


# ---------------------------------------------------------------------------
# Risk-flag registry helpers
# ---------------------------------------------------------------------------


def test_risk_flag_registry_helpers():
    assert is_registered_risk_flag("touches_secrets")
    assert not is_registered_risk_flag("not_a_real_flag")
    assert risk_flag_level("touches_secrets") is RiskLevel.CRITICAL
    assert risk_flag_level("touches_docs") is RiskLevel.LOW
    with pytest.raises(UnknownRiskFlagError):
        risk_flag_level("not_a_real_flag")


def test_detect_risk_flags_only_registered():
    flags = detect_risk_flags("delete the credentials and deploy")
    # Every detected flag is registered.
    assert all(f in RISK_FLAG_REGISTRY for f in flags)
    assert "deletes_data" in flags
    assert "touches_credentials" in flags
    assert "external_side_effect" in flags
    # Sorted + de-duplicated.
    assert flags == sorted(set(flags))


# ---------------------------------------------------------------------------
# Autonomy-mode interpretation + record wiring
# ---------------------------------------------------------------------------


def test_autonomy_mode_interpretation():
    dock = FrontDock(tip_client=None)
    result = dock.intake("Implement the feature", autonomy_mode="advisory")
    assert result.job.autonomy_mode is AutonomyMode.ADVISORY
    assert result.manifest.permissions.autonomy_mode is AutonomyMode.ADVISORY
    # Default mode when unspecified.
    default_result = dock.intake("Implement the feature")
    assert default_result.job.autonomy_mode is AutonomyMode.DISPATCH_WITH_APPROVAL


def test_job_default_status_is_draft_and_ids_wired():
    dock = FrontDock(tip_client=None)
    result = dock.intake(
        "Implement the feature",
        job_id="job_explicit_1",
        manifest_id="manifest_explicit_1",
        source_task_packet_id="TP-123",
    )
    assert result.job.status is DispatchJobStatus.DRAFT
    assert result.job.id == "job_explicit_1"
    assert result.manifest.id == "manifest_explicit_1"
    assert result.manifest.job_id == "job_explicit_1"
    assert result.job.source_task_packet_id == "TP-123"


def test_derived_ids_have_expected_prefixes():
    dock = FrontDock(tip_client=None)
    result = dock.intake("Implement the feature")
    assert result.job.id.startswith("job_")
    assert result.manifest.id.startswith("manifest_")


def test_decision_job_id_matches_job():
    dock = FrontDock(tip_client=None)
    result = dock.intake("Rotate the secret token", job_id="job_xyz")
    assert result.is_blocked
    assert result.decision.job_id == "job_xyz"
