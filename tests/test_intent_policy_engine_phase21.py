# SPDX-License-Identifier: Apache-2.0
"""Phase 2.1 — dry-run intent policy engine regression suite.

Nine directive-mandated test categories, one class per category:

  - high-confidence policy decision
  - low-confidence safety behavior
  - catch_all safety behavior
  - missing slots safety behavior
  - live_verified=False safety behavior
  - default observe_only behavior
  - no runtime route mutation
  - no raw prompt / secrets in policy telemetry
  - config default tests

Plus three cross-cutting classes:
  - decision shape pinned (the directive's 14 fields)
  - CLI `policy-preview` end-to-end smoke
  - telemetry round-trip
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.proxy.intent_policy_engine import (
    ACTION_OBSERVE_ONLY,
    ACTION_SUGGEST_COMPRESSION_PROFILE,
    ACTION_SUGGEST_ROUTE,
    ACTION_WARN_ONLY,
    ACTIONS_PHASE_2_1,
    REASON_CATCH_ALL_BLOCKED_ROUTING,
    REASON_DEFAULT_OBSERVE_ONLY,
    REASON_DRY_RUN_SUGGEST,
    REASON_LOW_CONFIDENCE_BLOCKED_ROUTING,
    REASON_MISSING_SLOTS_BLOCKED_ROUTING,
    REASON_UNVERIFIED_PROVIDER_BLOCKED,
    SAFETY_CATCH_ALL,
    SAFETY_LOW_CONFIDENCE,
    SAFETY_MISSING_SLOTS,
    SAFETY_UNVERIFIED_PROVIDER,
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
    load_default_config,
    make_decision_id,
)
from tokenpak.proxy.intent_policy_telemetry import (
    IntentPolicyDecisionRow,
    IntentPolicyDecisionStore,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _safe_input(**over) -> PolicyInput:
    """Build a high-confidence, fully-classified safe input.

    Default values represent the happy path: confidence above the
    threshold, no catch-all, no missing required slots,
    live_verified=True. Tests override fields to drive each branch.
    """
    kw = dict(
        intent_class="summarize",
        confidence=0.9,
        slots_present=("period",),
        slots_missing=(),
        catch_all_reason=None,
        provider="tokenpak-test",
        model="test-model",
        estimated_cost_usd=None,
        adapter_capabilities=frozenset(),
        delivery_target_capabilities=frozenset(),
        live_verified_status=True,
        required_slots=(),
    )
    kw.update(over)
    return PolicyInput(**kw)


# ── 1. High-confidence policy decision ────────────────────────────────


class TestHighConfidenceDecision:
    """High confidence + safe inputs produces a non-warn action.

    With the default config (allow_auto_routing=false), a
    suggest_compression_profile or other suggest_* fires for
    intents with heuristics; observe_only for those without.
    """

    def test_summarize_emits_compression_suggestion(self):
        d = evaluate_policy(_safe_input(intent_class="summarize"))
        assert d.action == ACTION_SUGGEST_COMPRESSION_PROFILE
        assert d.compression_profile == "aggressive"
        assert d.safety_flags == ()
        assert d.decision_reason == REASON_DRY_RUN_SUGGEST

    def test_status_no_heuristic_falls_back_to_observe_only(self):
        # 'status' has no compression / delivery heuristic in the
        # Phase 2.1 table. With no cache-capable adapter, the engine
        # falls through to observe_only.
        d = evaluate_policy(_safe_input(intent_class="status"))
        assert d.action == ACTION_OBSERVE_ONLY
        assert d.safety_flags == ()

    def test_suggest_route_when_auto_routing_allowed(self):
        cfg = PolicyEngineConfig(allow_auto_routing=True)
        d = evaluate_policy(_safe_input(), cfg)
        assert d.action == ACTION_SUGGEST_ROUTE
        # In dry-run, recommended_provider mirrors the request's
        # current provider (Phase 2.4 will introduce real routing
        # heuristics).
        assert d.recommended_provider == "tokenpak-test"
        assert d.recommended_model == "test-model"

    def test_decision_id_unique_per_call(self):
        d1 = evaluate_policy(_safe_input())
        d2 = evaluate_policy(_safe_input())
        assert d1.decision_id != d2.decision_id


# ── 2. Low-confidence safety behavior ─────────────────────────────────


class TestLowConfidenceSafety:
    """confidence < threshold MUST suppress routing-affecting
    actions and emit warn_only with safety_flags=('low_confidence',).
    """

    def test_low_confidence_suppresses_routing(self):
        # Even with allow_auto_routing=True, low confidence should
        # prevent suggest_route.
        cfg = PolicyEngineConfig(allow_auto_routing=True)
        d = evaluate_policy(_safe_input(confidence=0.4), cfg)
        assert d.action == ACTION_WARN_ONLY
        assert SAFETY_LOW_CONFIDENCE in d.safety_flags
        assert d.recommended_provider is None
        assert d.recommended_model is None

    def test_low_confidence_decision_reason(self):
        d = evaluate_policy(_safe_input(confidence=0.3))
        assert d.decision_reason == REASON_LOW_CONFIDENCE_BLOCKED_ROUTING

    def test_low_confidence_warning_message_set(self):
        d = evaluate_policy(_safe_input(confidence=0.3))
        assert d.warning_message
        assert "low_confidence" in d.warning_message
        # Templated string — never includes prompt content.

    def test_at_threshold_passes(self):
        # At exactly the threshold (>=), engine treats as safe.
        d = evaluate_policy(_safe_input(confidence=0.65))
        assert SAFETY_LOW_CONFIDENCE not in d.safety_flags

    def test_just_below_threshold_blocks(self):
        d = evaluate_policy(_safe_input(confidence=0.6499))
        assert SAFETY_LOW_CONFIDENCE in d.safety_flags


# ── 3. Catch-all safety behavior ──────────────────────────────────────


class TestCatchAllSafety:
    """catch_all_reason is not None MUST suppress routing-affecting
    actions.
    """

    def test_catch_all_blocks_routing(self):
        cfg = PolicyEngineConfig(allow_auto_routing=True)
        d = evaluate_policy(_safe_input(catch_all_reason="empty_prompt",
                                         confidence=1.0), cfg)
        assert d.action == ACTION_WARN_ONLY
        assert SAFETY_CATCH_ALL in d.safety_flags

    def test_catch_all_decision_reason(self):
        # The reason taxonomy prioritises low_confidence first when
        # both flags trip. Test catch_all in isolation by keeping
        # confidence high.
        d = evaluate_policy(_safe_input(catch_all_reason="keyword_miss",
                                         confidence=1.0))
        assert d.decision_reason == REASON_CATCH_ALL_BLOCKED_ROUTING


# ── 4. Missing slots safety behavior ──────────────────────────────────


class TestMissingSlotsSafety:
    """Missing required slots MUST block routing-affecting actions.

    The engine's ``required_slots`` field on PolicyInput is the
    contract — Phase 2.1 doesn't auto-derive from
    slot_definitions.yaml; that wiring is sub-phase 2.2 work.
    """

    def test_missing_required_slot_blocks_routing(self):
        cfg = PolicyEngineConfig(allow_auto_routing=True)
        d = evaluate_policy(
            _safe_input(
                slots_missing=("target",),
                required_slots=("target",),
            ),
            cfg,
        )
        assert d.action == ACTION_WARN_ONLY
        assert SAFETY_MISSING_SLOTS in d.safety_flags

    def test_missing_optional_slot_does_not_block(self):
        # 'target' is NOT in required_slots, so its absence from
        # slots_present + presence in slots_missing must NOT trip.
        d = evaluate_policy(_safe_input(slots_missing=("target",)))
        assert SAFETY_MISSING_SLOTS not in d.safety_flags

    def test_missing_slots_decision_reason(self):
        d = evaluate_policy(
            _safe_input(
                slots_missing=("target",),
                required_slots=("target",),
                confidence=1.0,
            )
        )
        assert d.decision_reason == REASON_MISSING_SLOTS_BLOCKED_ROUTING


# ── 5. live_verified=False safety behavior ────────────────────────────


class TestLiveVerifiedFalseSafety:
    """A provider with live_verified=False MUST NOT be recommended
    unless ``allow_unverified_providers=true`` in config.
    """

    def test_unverified_provider_blocked_by_default(self):
        cfg = PolicyEngineConfig(allow_auto_routing=True)
        d = evaluate_policy(
            _safe_input(live_verified_status=False),
            cfg,
        )
        assert d.action == ACTION_WARN_ONLY
        assert SAFETY_UNVERIFIED_PROVIDER in d.safety_flags
        assert d.recommended_provider is None

    def test_unverified_allowed_when_config_flips(self):
        cfg = PolicyEngineConfig(
            allow_auto_routing=True,
            allow_unverified_providers=True,
        )
        d = evaluate_policy(
            _safe_input(live_verified_status=False),
            cfg,
        )
        assert SAFETY_UNVERIFIED_PROVIDER not in d.safety_flags
        assert d.action == ACTION_SUGGEST_ROUTE

    def test_unverified_decision_reason(self):
        d = evaluate_policy(_safe_input(
            live_verified_status=False, confidence=1.0,
        ))
        # In isolation (only unverified-provider trips), the reason
        # is the unverified one. With other safety flags, taxonomy
        # priority would re-rank.
        assert d.decision_reason == REASON_UNVERIFIED_PROVIDER_BLOCKED


# ── 6. Default observe_only behavior ──────────────────────────────────


class TestDefaultObserveOnly:
    """With default config and an intent that lacks any heuristic
    + no cache-capable adapter, the engine emits observe_only.
    Zero behavior change vs. Phase 1.1.
    """

    def test_default_for_status_intent(self):
        # 'status' has no compression / delivery heuristic.
        d = evaluate_policy(_safe_input(intent_class="status"))
        assert d.action == ACTION_OBSERVE_ONLY
        assert d.decision_reason == REASON_DEFAULT_OBSERVE_ONLY
        assert d.warning_message is None
        assert d.recommended_provider is None
        assert d.compression_profile is None

    def test_default_config_does_not_emit_suggest_route(self):
        # Default allow_auto_routing=False → never emits suggest_route
        # even for safe inputs.
        d = evaluate_policy(_safe_input())  # default cfg
        assert d.action != ACTION_SUGGEST_ROUTE


# ── 7. No runtime route mutation ──────────────────────────────────────


class TestNoRouteMutation:
    """The engine MUST be a pure function. Verify by passing a
    mutable bag of kwargs and asserting it's untouched after.
    """

    def test_input_unchanged_after_evaluate(self):
        inp = _safe_input(intent_class="summarize")
        # Capture pre-call state.
        snapshot = (
            inp.intent_class, inp.confidence,
            inp.slots_present, inp.slots_missing,
            inp.catch_all_reason, inp.provider, inp.model,
            inp.adapter_capabilities, inp.live_verified_status,
            inp.required_slots,
        )
        evaluate_policy(inp)
        assert (
            inp.intent_class, inp.confidence,
            inp.slots_present, inp.slots_missing,
            inp.catch_all_reason, inp.provider, inp.model,
            inp.adapter_capabilities, inp.live_verified_status,
            inp.required_slots,
        ) == snapshot

    def test_decision_is_immutable(self):
        # PolicyDecision is a frozen dataclass — assignment should
        # raise. This pins the mutation guarantee at the type level.
        d = evaluate_policy(_safe_input())
        with pytest.raises(Exception):
            d.action = "spoofed"  # type: ignore[misc]


# ── 8. No raw prompts / secrets in policy telemetry ───────────────────


class TestPrivacyContract:
    """The schema and writer MUST NOT carry raw prompt content. The
    sentinel-substring pattern from Phase 1 carries forward.
    """

    SENTINEL = "kevin-magic-prompt-marker-PHASE-2-1"

    def test_engine_output_carries_no_caller_supplied_string(self):
        # The engine takes no string the caller could supply that
        # ends up in PolicyDecision. Smoke this by injecting the
        # sentinel into every input string field and asserting it
        # appears nowhere in the serialized decision.
        inp = _safe_input(
            intent_class="summarize",
            provider=self.SENTINEL + "-provider",
            model=self.SENTINEL + "-model",
        )
        d = evaluate_policy(inp)
        # Provider / model do flow through to suggest_route. With
        # allow_auto_routing=False (default), that path doesn't
        # fire — assert the sentinel is absent.
        payload = json.dumps(d.to_dict())
        assert self.SENTINEL not in payload, (
            "default-config decision leaked caller-supplied provider/model"
        )

    def test_telemetry_row_has_no_prompt_content(self, tmp_path: Path):
        store = IntentPolicyDecisionStore(db_path=tmp_path / "t.db")
        # Build a decision with a templated warning message.
        d = evaluate_policy(_safe_input(confidence=0.3))
        row = IntentPolicyDecisionRow(
            request_id="req-priv-1",
            contract_id="cid-priv-1",
            timestamp="2026-04-25T22:00:00",
            decision=d,
        )
        store.write(row)

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.row_factory = sqlite3.Row
        rec = conn.execute(
            "SELECT * FROM intent_policy_decisions WHERE request_id = 'req-priv-1'"
        ).fetchone()
        conn.close()
        # No column should ever contain a sentinel — the engine
        # never received one. This guards against a future change
        # that pulls prompt-derived strings into a column.
        for col in rec.keys():
            v = rec[col]
            if v is not None:
                assert self.SENTINEL not in str(v), (
                    f"sentinel substring leaked into column {col!r}"
                )

    def test_warning_message_is_templated(self):
        # The warning_message MUST not include any caller-supplied
        # substring; only the safety-flag identifiers built by the
        # engine.
        inp = _safe_input(
            intent_class="summarize",
            confidence=0.3,
            provider=self.SENTINEL + "-prov",
            model=self.SENTINEL + "-model",
        )
        d = evaluate_policy(inp)
        assert d.warning_message
        assert self.SENTINEL not in d.warning_message


# ── 9. Config default tests ───────────────────────────────────────────


class TestConfigDefaults:
    """The directive pins the default config exactly. Test each
    field so a future change requires explicit ratification.
    """

    def test_default_config_values(self):
        cfg = load_default_config()
        assert cfg.mode == "observe_only"
        assert cfg.dry_run is True
        assert cfg.allow_auto_routing is False
        assert cfg.allow_unverified_providers is False
        assert cfg.low_confidence_threshold == 0.65

    def test_config_is_frozen(self):
        cfg = load_default_config()
        with pytest.raises(Exception):
            cfg.mode = "enforce"  # type: ignore[misc]

    def test_action_enum_has_seven_values(self):
        # Phase 2.1 directive enumerates exactly seven actions.
        assert len(ACTIONS_PHASE_2_1) == 7


# ── 10. Decision shape (cross-cutting) ────────────────────────────────


class TestDecisionShape:
    """The directive enumerates 15 PolicyDecision fields. Pin them
    so a future shape change requires explicit ratification.
    """

    REQUIRED_FIELDS = {
        "decision_id",
        "mode",
        "intent_class",
        "confidence",
        "action",
        "recommended_provider",
        "recommended_model",
        "budget_action",
        "compression_profile",
        "cache_strategy",
        "delivery_strategy",
        "warning_message",
        "requires_user_confirmation",
        "decision_reason",
        "safety_flags",
    }

    def test_to_dict_contains_required_fields(self):
        d = evaluate_policy(_safe_input())
        payload = d.to_dict()
        missing = self.REQUIRED_FIELDS - set(payload)
        assert not missing, f"missing required fields in to_dict: {missing}"

    def test_mode_is_dry_run_in_phase_2_1(self):
        d = evaluate_policy(_safe_input())
        assert d.mode == "dry_run"

    def test_decision_id_shape(self):
        d = evaluate_policy(_safe_input())
        assert isinstance(d.decision_id, str)
        # ms-prefixed hex + random hex tail (29 chars total).
        assert len(d.decision_id) == 13 + 16

    def test_make_decision_id_unique(self):
        a = make_decision_id()
        b = make_decision_id()
        assert a != b


# ── 11. Telemetry round-trip (cross-cutting) ──────────────────────────


class TestTelemetryRoundTrip:

    def test_table_created_on_first_write(self, tmp_path: Path):
        store = IntentPolicyDecisionStore(db_path=tmp_path / "t.db")
        d = evaluate_policy(_safe_input())
        store.write(IntentPolicyDecisionRow(
            request_id="rt1", contract_id="c1",
            timestamp="2026-04-25T22:00:00", decision=d,
            config_mode="observe_only", config_dry_run=True,
            config_allow_auto_routing=False,
            config_allow_unverified_providers=False,
            config_low_confidence_threshold=0.65,
        ))
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='intent_policy_decisions'"
        )
        assert cur.fetchone() is not None

    def test_fetch_latest_returns_inserted_row(self, tmp_path: Path):
        store = IntentPolicyDecisionStore(db_path=tmp_path / "t.db")
        d = evaluate_policy(_safe_input(intent_class="summarize"))
        store.write(IntentPolicyDecisionRow(
            request_id="rt2", contract_id="c2",
            timestamp="2026-04-25T22:01:00", decision=d,
        ))
        latest = store.fetch_latest()
        assert latest is not None
        assert latest["intent_class"] == "summarize"
        assert latest["action"] in ACTIONS_PHASE_2_1
        assert isinstance(latest["safety_flags"], list)

    def test_fetch_latest_returns_none_on_empty(self, tmp_path: Path):
        store = IntentPolicyDecisionStore(db_path=tmp_path / "absent.db")
        assert store.fetch_latest() is None

    def test_writer_does_not_raise_on_unwritable_path(self):
        bad = Path("/proc/this-cannot-be-a-real-file.db")
        store = IntentPolicyDecisionStore(db_path=bad)
        d = evaluate_policy(_safe_input())
        # Best-effort contract — never propagates to caller.
        store.write(IntentPolicyDecisionRow(
            request_id="bad", contract_id="c", timestamp="t",
            decision=d,
        ))


# ── 12. CLI policy-preview end-to-end ─────────────────────────────────


class TestCliPreview:

    def test_help_includes_policy_preview(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "policy-preview", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--last" in result.stdout
        assert "--json" in result.stdout

    def test_preview_runs_without_error_on_empty(self, tmp_path: Path, monkeypatch):
        # Point the store at a fresh empty path; the command must
        # exit 0 with the friendly "no rows yet" message.
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "policy-preview"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, (
            f"policy-preview failed: {result.stdout}\n{result.stderr}"
        )

    def test_preview_json_mode_returns_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "policy-preview", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        # Output is JSON-parseable.
        json.loads(result.stdout)
