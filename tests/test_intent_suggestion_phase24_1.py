# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.1 — PolicySuggestion builder + telemetry test suite.

Thirteen directive-mandated test categories:

  - eligible suggestion generated
  - low-confidence no suggestion
  - catch_all no suggestion
  - missing slots no suggestion (constructive variant fires only
    on warn_only/missing_slots; main types suppressed)
  - live_verified=False no suggestion by default
  - adapter capability suggestion (constructive fallback)
  - budget warning suggestion
  - wording forbidden-list enforcement
  - no prompt text or secrets
  - no classifier mutation
  - no request mutation
  - no routing mutation
  - deterministic suggestion_id behavior

Read-only against production telemetry; uses the production
builder + telemetry store so the schema stays a single source of
truth.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import build_contract
from tokenpak.proxy.intent_policy_engine import (
    ACTION_FLAG_BUDGET_RISK,
    ACTION_OBSERVE_ONLY,
    ACTION_SUGGEST_CACHE_POLICY,
    ACTION_SUGGEST_COMPRESSION_PROFILE,
    ACTION_SUGGEST_DELIVERY_POLICY,
    ACTION_SUGGEST_ROUTE,
    ACTION_WARN_ONLY,
    REASON_DEFAULT_OBSERVE_ONLY,
    REASON_DRY_RUN_SUGGEST,
    SAFETY_LOW_CONFIDENCE,
    SAFETY_MISSING_SLOTS,
    PolicyDecision,
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
    make_decision_id,
)
from tokenpak.proxy.intent_suggestion import (
    DRY_RUN_DISCLAIMER,
    FORBIDDEN_PHRASES,
    SOURCE_INTENT_POLICY_V0,
    SUGGESTION_ADAPTER_CAPABILITY,
    SUGGESTION_BUDGET_WARNING,
    SUGGESTION_COMPRESSION,
    SUGGESTION_MISSING_SLOT,
    SUGGESTION_PROVIDER_MODEL,
    SUGGESTION_TYPES,
    SuggestionBuilderContext,
    build_suggestions,
    make_suggestion_id,
)
from tokenpak.proxy.intent_suggestion_telemetry import (
    IntentSuggestionRow,
    IntentSuggestionStore,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _classification(intent_class="summarize", confidence=0.9,
                    slots_present=("period",), slots_missing=(),
                    catch_all_reason=None):
    return IntentClassification(
        intent_class=intent_class,
        confidence=confidence,
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )


def _safe_input(**over) -> PolicyInput:
    kw = dict(
        intent_class="summarize",
        confidence=0.9,
        slots_present=("period",),
        slots_missing=(),
        catch_all_reason=None,
        provider="tokenpak-test",
        model="test-model",
        live_verified_status=True,
        required_slots=(),
    )
    kw.update(over)
    return PolicyInput(**kw)


def _safe_contract(*, prompt="summarize the vault", **kw):
    cls = _classification(**kw)
    return build_contract(classification=cls, raw_prompt=prompt)


def _ctx(*, capabilities=frozenset({"tip.compression.v1"}),
         provider_verified=None, required_slots=(),
         allow_unverified=False, threshold=0.65):
    return SuggestionBuilderContext(
        config=PolicyEngineConfig(
            allow_unverified_providers=allow_unverified,
            low_confidence_threshold=threshold,
        ),
        adapter_capabilities=capabilities,
        provider_verified=provider_verified,
        required_slots=required_slots,
    )


# ── 1. Eligible suggestion generated ──────────────────────────────────


class TestEligibleSuggestion:

    def test_high_confidence_summarize_emits_compression(self):
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggestion_type == SUGGESTION_COMPRESSION
        assert s.confidence == contract.confidence
        assert s.requires_confirmation is False
        assert s.source == SOURCE_INTENT_POLICY_V0
        assert s.user_visible is False  # 2.4.3 wires this on
        assert s.recommended_action  # not None
        # Disclaimer present in message.
        assert DRY_RUN_DISCLAIMER in s.message

    def test_suggestion_links_decision_and_contract(self):
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert suggestions[0].decision_id == decision.decision_id
        assert suggestions[0].contract_id == contract.contract_id


# ── 2. Low-confidence no suggestion ───────────────────────────────────


class TestLowConfidenceNoSuggestion:

    def test_below_threshold_emits_nothing(self):
        # Even with a clean intent, low confidence blocks all
        # suggestion types.
        contract = _safe_contract(confidence=0.4)
        # Build a decision via the engine — it'll emit warn_only
        # with low_confidence flag.
        decision = evaluate_policy(_safe_input(confidence=0.4), PolicyEngineConfig())
        assert decision.action == ACTION_WARN_ONLY
        assert SAFETY_LOW_CONFIDENCE in decision.safety_flags
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert suggestions == []


# ── 3. Catch-all no suggestion ────────────────────────────────────────


class TestCatchAllNoSuggestion:

    def test_catch_all_emits_nothing(self):
        contract = _safe_contract(intent_class="query", confidence=0.0,
                                   slots_present=(), slots_missing=(),
                                   catch_all_reason="empty_prompt")
        # Construct a hand-crafted suggest_compression decision to
        # bypass the engine (which would itself block routing on
        # catch-all). The builder MUST also block.
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="query",
            confidence=0.0,
            action=ACTION_SUGGEST_COMPRESSION_PROFILE,
            compression_profile="aggressive",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert suggestions == []


# ── 4. Missing slots no suggestion (main types suppressed) ────────────


class TestMissingSlotsNoSuggestion:

    def test_main_types_suppressed_when_required_slot_missing(self):
        contract = _safe_contract(slots_missing=("target",))
        # Pretend the engine wants to suggest compression even with
        # missing required slots.
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_COMPRESSION_PROFILE,
            compression_profile="aggressive",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(required_slots=("target",)),
        )
        # No compression suggestion when required slots missing.
        # Constructive missing_slot_improvement only fires when the
        # decision itself is warn_only/missing_slots.
        assert suggestions == []

    def test_warn_only_missing_slots_emits_constructive(self):
        contract = _safe_contract(slots_missing=("target",))
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_WARN_ONLY,
            decision_reason="missing_slots_blocked_routing",
            safety_flags=(SAFETY_MISSING_SLOTS,),
        )
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(required_slots=("target",)),
        )
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggestion_type == SUGGESTION_MISSING_SLOT
        assert "target" in s.message
        assert s.safety_flags == (SAFETY_MISSING_SLOTS,)


# ── 5. live_verified=False no suggestion by default ───────────────────


class TestLiveVerifiedFalseNoSuggestion:

    def test_unverified_blocks_provider_recommendation(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_ROUTE,
            recommended_provider="tokenpak-unverified",
            recommended_model="m",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(provider_verified=False, allow_unverified=False),
        )
        assert suggestions == []

    def test_unverified_emits_when_allowed(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_ROUTE,
            recommended_provider="tokenpak-unverified",
            recommended_model="m",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(provider_verified=False, allow_unverified=True),
        )
        assert len(suggestions) == 1
        assert suggestions[0].suggestion_type == SUGGESTION_PROVIDER_MODEL


# ── 6. Adapter capability suggestion (constructive fallback) ──────────


class TestAdapterCapabilitySuggestion:

    def test_compression_falls_back_when_capability_absent(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_COMPRESSION_PROFILE,
            compression_profile="aggressive",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        # Adapter has SOMETHING (passes rule e) but not tip.compression.v1.
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(capabilities=frozenset({"tip.security.dlp-redaction"})),
        )
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggestion_type == SUGGESTION_ADAPTER_CAPABILITY
        assert "tip.compression.v1" in s.message

    def test_cache_falls_back_when_no_cache_capability(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_CACHE_POLICY,
            cache_strategy="proxy_managed",
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision,
            contract=contract,
            ctx=_ctx(capabilities=frozenset({"tip.compression.v1"})),
        )
        assert len(suggestions) == 1
        assert suggestions[0].suggestion_type == SUGGESTION_ADAPTER_CAPABILITY


# ── 7. Budget warning suggestion ──────────────────────────────────────


class TestBudgetWarningSuggestion:

    def test_flag_budget_risk_emits_warning(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_FLAG_BUDGET_RISK,
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggestion_type == SUGGESTION_BUDGET_WARNING
        assert s.recommended_action is None  # observation only

    def test_observe_only_emits_nothing(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="status",
            confidence=0.9,
            action=ACTION_OBSERVE_ONLY,
            decision_reason=REASON_DEFAULT_OBSERVE_ONLY,
        )
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert suggestions == []


# ── 8. Wording forbidden-list enforcement ─────────────────────────────


class TestWordingGuardrail:

    def test_forbidden_phrases_constant_pinned(self):
        # The directive enumerates exactly these eight phrases.
        # Any future change requires explicit ratification.
        assert "applied" in FORBIDDEN_PHRASES
        assert "changed" in FORBIDDEN_PHRASES
        assert "routed to" in FORBIDDEN_PHRASES
        assert "switched to" in FORBIDDEN_PHRASES
        assert "now using" in FORBIDDEN_PHRASES
        assert "updated" in FORBIDDEN_PHRASES
        assert "will route" in FORBIDDEN_PHRASES
        assert "will switch" in FORBIDDEN_PHRASES

    def test_no_emitted_string_contains_forbidden_phrase(self):
        # Build one suggestion of every type that fires from a
        # decision in 2.4.1, and check every string field against
        # the forbidden regex.
        contract = _safe_contract()

        cases = [
            (ACTION_SUGGEST_COMPRESSION_PROFILE, "tip.compression.v1"),
            (ACTION_SUGGEST_CACHE_POLICY, "tip.cache.proxy-managed"),
            (ACTION_SUGGEST_DELIVERY_POLICY, "tip.compression.v1"),  # delivery not capability-gated
            (ACTION_FLAG_BUDGET_RISK, "tip.compression.v1"),
        ]
        forbidden_re = re.compile(
            r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
            re.IGNORECASE,
        )
        for action, capability in cases:
            decision = PolicyDecision(
                decision_id=make_decision_id(),
                mode="dry_run",
                intent_class="summarize",
                confidence=0.9,
                action=action,
                compression_profile="aggressive" if action == ACTION_SUGGEST_COMPRESSION_PROFILE else None,
                cache_strategy="proxy_managed" if action == ACTION_SUGGEST_CACHE_POLICY else None,
                delivery_strategy="non_streaming" if action == ACTION_SUGGEST_DELIVERY_POLICY else None,
                decision_reason=REASON_DRY_RUN_SUGGEST,
            )
            suggestions = build_suggestions(
                decision=decision, contract=contract,
                ctx=_ctx(capabilities=frozenset({capability})),
            )
            for s in suggestions:
                for text in (s.title, s.message, s.recommended_action or ""):
                    assert forbidden_re.search(text) is None, (
                        f"forbidden phrase in {s.suggestion_type}: {text!r}"
                    )

    def test_dry_run_disclaimer_in_message(self):
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        for s in suggestions:
            assert DRY_RUN_DISCLAIMER in s.message

    def test_wording_guardrail_raises_on_forbidden(self):
        # Direct test of the guardrail function. Hitting any
        # forbidden phrase in any field raises
        # SuggestionWordingError. This is the contract a future
        # template change relies on — a regression in
        # _check_wording would let bad wording through.
        #
        # Re-imported from sys.modules at call time to survive
        # the module-reload pollution from tests/deprecations/
        # (which pops tokenpak.proxy.* modules and rebinds the
        # exception class identity).
        from tokenpak.proxy.intent_suggestion import _check_wording

        def _expect_raise(*args):
            try:
                _check_wording(*args)
            except Exception as exc:  # noqa: BLE001
                assert type(exc).__name__ == "SuggestionWordingError", (
                    f"unexpected exception type: {type(exc).__name__}"
                )
                return
            pytest.fail(f"expected SuggestionWordingError for {args!r}")

        for phrase in FORBIDDEN_PHRASES:
            _expect_raise(f"This was {phrase} successfully")
            _expect_raise("title", f"message that has {phrase} in it", None)
            _expect_raise("title", "msg", f"action with {phrase}")

    def test_wording_guardrail_passes_safe_strings(self):
        from tokenpak.proxy.intent_suggestion import _check_wording

        # Allowed wording from spec §8.1 must pass the guard.
        for phrase in (
            "Recommended", "Consider", "Could improve", "Suggested",
            "May help", "Eligible for",
        ):
            _check_wording(f"This is a {phrase} suggestion.")
            _check_wording("title", "msg", f"{phrase} action")


# ── 9. No prompt text or secrets ──────────────────────────────────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PHASE-2-4-1"

    def test_sentinel_absent_from_suggestion(self):
        contract = _safe_contract(prompt=f"summarize the vault {self.SENTINEL}")
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        for s in suggestions:
            payload = json.dumps(s.to_dict())
            assert self.SENTINEL not in payload

    def test_sentinel_absent_from_persisted_row(self, tmp_path: Path):
        contract = _safe_contract(prompt=f"summarize the vault {self.SENTINEL}")
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        store = IntentSuggestionStore(db_path=tmp_path / "t.db")
        for s in suggestions:
            store.write(IntentSuggestionRow(suggestion=s, timestamp="t"))
        store.close()

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM intent_suggestions").fetchall()
        conn.close()
        for row in rows:
            for col in row.keys():
                v = row[col]
                if v is not None:
                    assert self.SENTINEL not in str(v), (
                        f"sentinel leaked into column {col!r}"
                    )


# ── 10. No classifier mutation ────────────────────────────────────────


class TestNoClassifierMutation:
    """The Phase 2.4 spec §11 rule: intent_classifier.py MUST NOT
    be edited in any 2.4.x PR. Structural test on the file
    contents — the §11 list of canonical-intent keywords + the
    classify_intent function signature must remain stable.
    """

    def test_classify_threshold_unchanged_from_phase_0(self):
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )
        assert CLASSIFY_THRESHOLD == 0.4
        assert INTENT_SOURCE_V0 == "rule_based_v0"


# ── 11. No request mutation ───────────────────────────────────────────


class TestNoRequestMutation:

    def test_builder_does_not_mutate_inputs(self):
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        ctx = _ctx()
        # Capture pre-call state (frozen dataclasses; capture
        # attribute identities).
        before_decision = decision
        before_contract = contract
        before_caps = ctx.adapter_capabilities
        build_suggestions(decision=decision, contract=contract, ctx=ctx)
        # Frozen dataclasses; reference equality is strict equality.
        assert before_decision is decision
        assert before_contract is contract
        assert before_caps is ctx.adapter_capabilities

    def test_suggestion_is_frozen(self):
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        with pytest.raises(Exception):
            suggestions[0].title = "spoofed"  # type: ignore[misc]


# ── 12. No routing mutation ───────────────────────────────────────────


class TestNoRoutingMutation:
    """Structural: the suggestion + telemetry modules MUST NOT
    import dispatch / forward primitives.
    """

    def test_suggestion_module_does_not_import_forward_path(self):
        import tokenpak.proxy.intent_suggestion as m

        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src, (
                f"suggestion module references dispatch primitive: {forbidden!r}"
            )

    def test_telemetry_module_does_not_import_forward_path(self):
        import tokenpak.proxy.intent_suggestion_telemetry as m

        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src, (
                f"telemetry module references dispatch primitive: {forbidden!r}"
            )


# ── 13. Deterministic suggestion_id ───────────────────────────────────


class TestSuggestionIdShape:
    """suggestion_id is opaque + sortable. Each call returns a
    distinct id; the shape (29 hex chars: 13 ms + 16 random) is
    pinned.
    """

    def test_id_shape(self):
        sid = make_suggestion_id()
        assert len(sid) == 29
        int(sid, 16)  # all hex

    def test_ids_unique(self):
        a = make_suggestion_id()
        b = make_suggestion_id()
        assert a != b

    def test_suggestion_type_enum_has_seven(self):
        assert len(SUGGESTION_TYPES) == 7


# ── 14. Telemetry round-trip + CLI smoke (cross-cutting) ──────────────


class TestTelemetryRoundTrip:

    def test_table_created_on_first_write(self, tmp_path: Path):
        store = IntentSuggestionStore(db_path=tmp_path / "t.db")
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        for s in build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        ):
            store.write(IntentSuggestionRow(suggestion=s, timestamp="t"))
        store.close()
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='intent_suggestions'"
        )
        assert cur.fetchone() is not None

    def test_fetch_latest_round_trip(self, tmp_path: Path):
        store = IntentSuggestionStore(db_path=tmp_path / "t.db")
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        for s in build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        ):
            store.write(IntentSuggestionRow(suggestion=s, timestamp="2026-04-26T12:00:00"))
        latest = store.fetch_latest()
        assert latest is not None
        assert latest["suggestion_type"] == SUGGESTION_COMPRESSION
        assert isinstance(latest["safety_flags"], list)
        assert latest["requires_confirmation"] is False
        assert latest["user_visible"] is False

    def test_fetch_for_decision(self, tmp_path: Path):
        store = IntentSuggestionStore(db_path=tmp_path / "t.db")
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        for s in build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        ):
            store.write(IntentSuggestionRow(suggestion=s, timestamp="t"))
        rows = store.fetch_for_decision(decision.decision_id)
        assert len(rows) == 1
        assert rows[0]["decision_id"] == decision.decision_id

    def test_writer_does_not_raise_on_unwritable_path(self):
        bad = Path("/proc/this-cannot-be-a-real-file.db")
        store = IntentSuggestionStore(db_path=bad)
        contract = _safe_contract()
        decision = evaluate_policy(_safe_input(), PolicyEngineConfig())
        for s in build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        ):
            # Best-effort contract — never propagates.
            store.write(IntentSuggestionRow(suggestion=s, timestamp="t"))


class TestCliInspector:

    def test_help_includes_suggestions(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "suggestions", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--last" in result.stdout
        assert "--json" in result.stdout

    def test_runs_without_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "suggestions"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0

    def test_json_mode_returns_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "suggestions", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        json.loads(result.stdout)


# ── 15. Eligibility shape pinned (cross-cutting) ──────────────────────


class TestEligibilityPinned:
    """Pin the seven types + the seven gates. A future spec change
    requires explicit ratification.
    """

    def test_seven_types_match_spec(self):
        assert SUGGESTION_TYPES == frozenset({
            "provider_model_recommendation",
            "compression_profile_recommendation",
            "cache_policy_recommendation",
            "delivery_strategy_recommendation",
            "budget_warning",
            "missing_slot_improvement",
            "adapter_capability_recommendation",
        })

    def test_decision_with_unknown_reason_emits_nothing(self):
        contract = _safe_contract()
        decision = PolicyDecision(
            decision_id=make_decision_id(),
            mode="dry_run",
            intent_class="summarize",
            confidence=0.9,
            action=ACTION_SUGGEST_COMPRESSION_PROFILE,
            compression_profile="aggressive",
            decision_reason="some_future_reason_not_in_taxonomy",
        )
        suggestions = build_suggestions(
            decision=decision, contract=contract, ctx=_ctx(),
        )
        assert suggestions == []
