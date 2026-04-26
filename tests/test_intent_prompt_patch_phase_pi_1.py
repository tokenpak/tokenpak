# SPDX-License-Identifier: Apache-2.0
"""Phase PI-1 — PromptPatch builder + telemetry test suite.

Nineteen directive-mandated test categories:

  1. PromptPatch object shape pinned
  2. builder returns zero when disabled
  3. builder returns patch when eligible
  4. low confidence blocks patch
  5. catch_all blocks patch
  6. missing slots produce only ask_clarification, not guidance
  7. byte-preserve locked blocks patch
  8. target=user_message rejected
  9. allow_byte_preserve_override clamped/blocked
  10. expired suggestion blocks patch
  11. patch text privacy guardrail
  12. forbidden wording guardrail
  13. no raw prompt stored
  14. no secrets emitted
  15. no request mutation
  16. no route mutation
  17. no classifier mutation
  18. CLI inspector empty DB behavior + populated DB behavior
  19. JSON shape

Read-only against production telemetry. Reuses production
classifier + contract + suggestion builder so the schema stays a
single source of truth.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import build_contract
from tokenpak.proxy.intent_policy_engine import (
    REASON_DRY_RUN_SUGGEST,
    SAFETY_MISSING_SLOTS,
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
)
from tokenpak.proxy.intent_prompt_patch import (
    ALL_MODES,
    FORBIDDEN_PHRASES,
    MODE_ASK_CLARIFICATION,
    MODE_INJECT_GUIDANCE,
    MODE_PREVIEW_ONLY,
    MODE_REWRITE_PROMPT,
    PATCH_TEXT_MAX_LEN,
    PI_1_ADDITIONAL_FORBIDDEN,
    PI_1_SUPPORTED_MODES,
    SOURCE_PI,
    TARGET_COMPANION_CONTEXT,
    TARGET_SYSTEM,
    TARGET_USER_MESSAGE,
    PromptInterventionConfig,
    PromptPatch,
    PromptPatchBuilderContext,
    build_patches,
    make_patch_id,
)
from tokenpak.proxy.intent_prompt_patch_telemetry import (
    IntentPatchRow,
    IntentPatchStore,
)
from tokenpak.proxy.intent_suggestion import (
    SuggestionBuilderContext,
    build_suggestions,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _classification(intent_class="create", confidence=0.9,
                    slots_present=("target",), slots_missing=(),
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
        intent_class="create", confidence=0.9,
        slots_present=("target",), slots_missing=(),
        catch_all_reason=None, provider="tokenpak-test",
        model="test-model", live_verified_status=True,
        required_slots=(),
    )
    kw.update(over)
    return PolicyInput(**kw)


def _make_eligible_inputs(*, intent_class="create", confidence=0.9,
                           slots_present=("target",), slots_missing=(),
                           catch_all_reason=None,
                           prompt="create a new file"):
    """Build (suggestion, contract, decision) for an eligible case.

    Goes through the production engine + suggestion builder so the
    schema stays a single source of truth. Returns the triple.
    """
    cls = _classification(
        intent_class=intent_class, confidence=confidence,
        slots_present=slots_present, slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )
    contract = build_contract(classification=cls, raw_prompt=prompt)

    inp = _safe_input(
        intent_class=intent_class, confidence=confidence,
        slots_present=slots_present, slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )
    decision = evaluate_policy(inp, PolicyEngineConfig())
    sugg_ctx = SuggestionBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=frozenset({"tip.compression.v1"}),
    )
    suggestions = build_suggestions(
        decision=decision, contract=contract, ctx=sugg_ctx,
    )
    suggestion = suggestions[0] if suggestions else None
    return suggestion, contract, decision


def _builder_ctx(*, capabilities=frozenset({"tip.compression.v1"}),
                 required_slots=()):
    return PromptPatchBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=capabilities,
        required_slots=required_slots,
    )


def _pi(**over) -> PromptInterventionConfig:
    kw = dict(
        enabled=True,
        mode=MODE_INJECT_GUIDANCE,
        target=TARGET_COMPANION_CONTEXT,
        require_confirmation=True,
        allow_byte_preserve_override=False,
    )
    kw.update(over)
    return PromptInterventionConfig(**kw)


# ── 1. PromptPatch object shape pinned ────────────────────────────────


class TestPromptPatchShape:

    REQUIRED_FIELDS = {
        "patch_id",
        "contract_id",
        "decision_id",
        "suggestion_id",
        "mode",
        "target",
        "original_hash",
        "patch_text",
        "reason",
        "confidence",
        "safety_flags",
        "requires_confirmation",
        "applied",
        "source",
    }

    def test_to_dict_has_all_required_fields(self):
        p = PromptPatch(
            patch_id="pid", contract_id="cid", decision_id="did",
            suggestion_id="sid", mode=MODE_PREVIEW_ONLY,
            target=TARGET_COMPANION_CONTEXT, original_hash="h",
            patch_text="<TokenPak Intent Guidance>x</TokenPak Intent Guidance>",
            reason="r", confidence=0.9,
        )
        d = p.to_dict()
        missing = self.REQUIRED_FIELDS - set(d)
        assert not missing, f"missing fields: {missing}"

    def test_default_values(self):
        p = PromptPatch(
            patch_id="pid", contract_id="cid", decision_id="did",
            suggestion_id="sid", mode=MODE_PREVIEW_ONLY,
            target=TARGET_COMPANION_CONTEXT, original_hash="h",
            patch_text="x", reason="r", confidence=0.9,
        )
        assert p.applied is False
        assert p.requires_confirmation is True
        assert p.source == SOURCE_PI
        assert p.safety_flags == ()

    def test_patch_is_frozen(self):
        p = PromptPatch(
            patch_id="pid", contract_id="cid", decision_id="did",
            suggestion_id="sid", mode=MODE_PREVIEW_ONLY,
            target=TARGET_COMPANION_CONTEXT, original_hash="h",
            patch_text="x", reason="r", confidence=0.9,
        )
        with pytest.raises(Exception):
            p.applied = True  # type: ignore[misc]

    def test_make_patch_id_shape(self):
        pid = make_patch_id()
        assert len(pid) == 29
        int(pid, 16)


# ── 2. builder returns zero when disabled ─────────────────────────────


class TestDisabledReturnsZero:

    def test_default_pi_config_is_disabled(self):
        cfg = PromptInterventionConfig()
        assert cfg.enabled is False

    def test_builder_returns_empty_when_disabled(self):
        suggestion, contract, decision = _make_eligible_inputs()
        assert suggestion is not None
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=PromptInterventionConfig(enabled=False),
            ctx=_builder_ctx(),
        )
        assert result == ()

    def test_builder_returns_patch_when_enabled_create(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create",
        )
        assert suggestion is not None
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert len(result) == 1


# ── 3. builder returns patch when eligible ────────────────────────────


class TestEligiblePatchEmitted:

    def test_create_intent_emits_coding_template(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create",
        )
        assert suggestion is not None
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert len(result) == 1
        p = result[0]
        assert "preserve the user's original request" in p.patch_text
        assert "<TokenPak Intent Guidance>" in p.patch_text
        assert p.applied is False

    def test_debug_intent_emits_debug_template(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="debug",
        )
        # The engine emits suggest_compression_profile for debug
        # (heuristic table). The suggestion itself doesn't matter
        # for the patch builder as long as decision_reason is in
        # the explainable set; the builder selects the template
        # by intent_class.
        if suggestion is None:
            pytest.skip("debug intent didn't produce a suggestion in this run")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert len(result) == 1
        p = result[0]
        assert "identify the failure path" in p.patch_text

    def test_intent_without_template_returns_zero(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="status",
        )
        # status produces observe_only — no suggestion. Use the
        # create-intent suggestion as the input but with status
        # contract; the builder selects template by intent_class.
        # Since status isn't in the coding set, no template.
        if suggestion is None:
            # Substitute manually.
            cls_create = _classification(intent_class="create")
            contract_create = build_contract(
                classification=cls_create, raw_prompt="create something",
            )
            inp_create = _safe_input(intent_class="create")
            decision_create = evaluate_policy(inp_create, PolicyEngineConfig())
            sugg_ctx = SuggestionBuilderContext(
                config=PolicyEngineConfig(),
                adapter_capabilities=frozenset({"tip.compression.v1"}),
            )
            suggs = build_suggestions(
                decision=decision_create, contract=contract_create,
                ctx=sugg_ctx,
            )
            suggestion = suggs[0]
        # Now use the suggestion with the status contract.
        cls_status = _classification(intent_class="status")
        contract_status = build_contract(
            classification=cls_status, raw_prompt="check status",
        )
        # decision.decision_reason needs to be explainable; reuse
        # the create decision's reason.
        result = build_patches(
            suggestion=suggestion, contract=contract_status, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 4. low confidence blocks patch ────────────────────────────────────


class TestLowConfidenceBlocks:

    def test_below_threshold_returns_empty(self):
        # Build via the engine with low confidence — the engine
        # itself emits warn_only, which doesn't produce a
        # suggestion. We construct the inputs manually to test
        # the builder gate independently.
        cls = _classification(intent_class="create", confidence=0.4)
        contract = build_contract(classification=cls, raw_prompt="create x")
        decision = evaluate_policy(
            _safe_input(intent_class="create", confidence=0.4),
            PolicyEngineConfig(),
        )
        # Build a stub suggestion the builder can read.
        from tokenpak.proxy.intent_suggestion import (
            SUGGESTION_COMPRESSION,
        )
        from tokenpak.proxy.intent_suggestion import (
            PolicySuggestion as _PS,
        )
        sugg = _PS(
            suggestion_id="s1", decision_id=decision.decision_id,
            contract_id=contract.contract_id,
            suggestion_type=SUGGESTION_COMPRESSION,
            title="t", message="m", recommended_action=None,
            confidence=0.4, safety_flags=(),
            requires_confirmation=False, user_visible=False,
            expires_at=None,
        )
        # Force the decision's reason to be explainable so the
        # only blocking gate is confidence.
        decision = replace(decision, decision_reason=REASON_DRY_RUN_SUGGEST)
        result = build_patches(
            suggestion=sugg, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 5. catch_all blocks patch ─────────────────────────────────────────


class TestCatchAllBlocks:

    def test_catch_all_returns_empty(self):
        suggestion, _, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline create-intent suggestion not produced")
        # Now substitute a catch-all contract.
        cls = _classification(
            intent_class="query", confidence=0.0,
            slots_present=(), slots_missing=(),
            catch_all_reason="empty_prompt",
        )
        contract = build_contract(classification=cls, raw_prompt="")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 6. missing slots produce only ask_clarification, not guidance ─────


class TestMissingSlotsAskClarification:

    def test_inject_guidance_blocks_when_required_slot_missing(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create", slots_missing=("target",),
        )
        if suggestion is None:
            pytest.skip("baseline create-intent suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(mode=MODE_INJECT_GUIDANCE),
            ctx=_builder_ctx(required_slots=("target",)),
        )
        assert result == ()

    def test_ask_clarification_emits_when_required_slot_missing(self):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create", slots_missing=("target",),
        )
        if suggestion is None:
            pytest.skip("baseline create-intent suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(mode=MODE_ASK_CLARIFICATION),
            ctx=_builder_ctx(required_slots=("target",)),
        )
        assert len(result) == 1
        p = result[0]
        assert "ask one clarifying question" in p.patch_text
        # Safety flag carries through.
        assert SAFETY_MISSING_SLOTS in p.safety_flags


# ── 7. byte-preserve locked blocks patch ──────────────────────────────


class TestByteReserveLocked:

    def test_byte_preserve_blocks_when_target_not_companion_context(self):
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(target=TARGET_SYSTEM),
            ctx=_builder_ctx(
                capabilities=frozenset({"tip.byte-preserved-passthrough"}),
            ),
        )
        assert result == ()

    def test_byte_preserve_allows_companion_context(self):
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        # Companion-context is the explicit exception per spec §6.
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(target=TARGET_COMPANION_CONTEXT),
            ctx=_builder_ctx(
                capabilities=frozenset({
                    "tip.byte-preserved-passthrough",
                    "tip.compression.v1",
                }),
            ),
        )
        assert len(result) == 1


# ── 8. target=user_message rejected ───────────────────────────────────


class TestUserMessageTargetRejected:

    def test_user_message_target_returns_empty(self):
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(target=TARGET_USER_MESSAGE),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 9. allow_byte_preserve_override clamped/blocked ───────────────────


class TestByteReserveOverrideBlocked:

    def test_override_requested_blocks_in_pi_1(self):
        # Even on a non-byte-preserved adapter, requesting the
        # override hard-blocks in PI-1.
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(allow_byte_preserve_override=True),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 10. expired suggestion blocks patch ───────────────────────────────


class TestExpiredSuggestionBlocked:

    def test_past_expires_at_blocks(self):
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        # Mutate the suggestion to have a past expires_at.
        expired = replace(suggestion, expires_at="2020-01-01T00:00:00")
        result = build_patches(
            suggestion=expired, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 11. patch text privacy guardrail ──────────────────────────────────


class TestPatchTextPrivacyGuardrail:

    def test_templates_pass_privacy_check(self):
        # The three templates ship clean. This is a regression
        # test against any future template that accidentally
        # interpolates a credential-shaped value.
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create",
        )
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert len(result) == 1
        # No credential-shaped substring.
        p = result[0]
        for cred_pattern in (
            "sk-",
            "sk-ant-",
            "AKIA",
            "github_pat_",
            "BEGIN PRIVATE KEY",
        ):
            assert cred_pattern not in p.patch_text


# ── 12. forbidden wording guardrail ───────────────────────────────────


class TestForbiddenWordingGuardrail:

    def test_pi_1_forbidden_phrases_pinned(self):
        # The directive enumerates exactly these phrases on top of
        # the Phase 2.4.1 list. Any future change requires
        # explicit ratification.
        for phrase in (
            "injected",
            "mutated",
            "rewrote",
            "inserted",
            "will inject",
            "will rewrite",
        ):
            assert phrase in [p.lower() for p in FORBIDDEN_PHRASES]
        for phrase in PI_1_ADDITIONAL_FORBIDDEN:
            assert phrase in FORBIDDEN_PHRASES

    def test_template_strings_have_no_forbidden_phrase(self):
        # Build one patch of each PI-1 supported template and
        # scan the emitted text.
        forbidden_re = re.compile(
            r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
            re.IGNORECASE,
        )
        cases = [
            ("create", MODE_INJECT_GUIDANCE),
            ("debug", MODE_INJECT_GUIDANCE),
            ("create", MODE_ASK_CLARIFICATION),
        ]
        for intent_class, mode in cases:
            suggestion, contract, decision = _make_eligible_inputs(
                intent_class=intent_class,
                slots_missing=("target",) if mode == MODE_ASK_CLARIFICATION else (),
            )
            if suggestion is None:
                continue
            result = build_patches(
                suggestion=suggestion, contract=contract, decision=decision,
                pi_config=_pi(mode=mode),
                ctx=_builder_ctx(
                    required_slots=(("target",) if mode == MODE_ASK_CLARIFICATION else ()),
                ),
            )
            for p in result:
                for text in (p.patch_text, p.reason):
                    m = forbidden_re.search(text)
                    assert m is None, (
                        f"forbidden phrase {m.group(0)!r} in PI-1 emit: {text!r}"
                    )


# ── 13 + 14. no raw prompt stored, no secrets emitted ─────────────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PI-1"

    def test_sentinel_absent_from_patch(self):
        # Inject the sentinel into the prompt; assert it appears
        # nowhere in the emitted patch.
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create",
            prompt=f"create a new file {self.SENTINEL}",
        )
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert len(result) == 1
        payload = json.dumps(result[0].to_dict())
        assert self.SENTINEL not in payload

    def test_sentinel_absent_from_persisted_row(self, tmp_path: Path):
        suggestion, contract, decision = _make_eligible_inputs(
            intent_class="create",
            prompt=f"create a new file {self.SENTINEL}",
        )
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        store = IntentPatchStore(db_path=tmp_path / "t.db")
        for p in result:
            store.write(IntentPatchRow(patch=p, created_at="t"))
        store.close()

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM intent_patches").fetchall()
        conn.close()
        for row in rows:
            for col in row.keys():
                v = row[col]
                if v is not None:
                    assert self.SENTINEL not in str(v)


# ── 15. no request mutation ───────────────────────────────────────────


class TestNoRequestMutation:

    def test_builder_does_not_mutate_inputs(self):
        suggestion, contract, decision = _make_eligible_inputs()
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        # Capture refs (frozen dataclasses).
        before = (suggestion, contract, decision)
        build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        assert before == (suggestion, contract, decision)


# ── 16. no route mutation ─────────────────────────────────────────────


class TestNoRouteMutation:

    def test_module_does_not_import_dispatch(self):
        import tokenpak.proxy.intent_prompt_patch as m
        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src

    def test_telemetry_module_does_not_import_dispatch(self):
        import tokenpak.proxy.intent_prompt_patch_telemetry as m
        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src


# ── 17. no classifier mutation ────────────────────────────────────────


class TestNoClassifierMutation:

    def test_classifier_constants_unchanged(self):
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )
        assert CLASSIFY_THRESHOLD == 0.4
        assert INTENT_SOURCE_V0 == "rule_based_v0"

    def test_pi_1_does_not_register_intent_classifier_path(self):
        # Structural: the patch module + telemetry module MUST NOT
        # import or invoke the classifier directly. They consume
        # the contract / decision / suggestion that came out of
        # the classifier upstream.
        for modname in (
            "tokenpak.proxy.intent_prompt_patch",
            "tokenpak.proxy.intent_prompt_patch_telemetry",
        ):
            __import__(modname)
            mod = sys.modules[modname]
            src = Path(mod.__file__).read_text()
            assert "classify_intent(" not in src
            assert "extract_prompt_text(" not in src


# ── 18. CLI inspector empty + populated ───────────────────────────────


class TestCliInspector:

    def test_help_includes_patches(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "patches", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--last" in result.stdout
        assert "--json" in result.stdout

    def test_runs_without_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "patches"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        # The four directive labels MUST appear when the table is
        # empty (which is the typical state for a fresh PI-1
        # install).
        for label in (
            "PREVIEW ONLY",
            "NOT APPLIED",
            "NO PROMPT MUTATION",
            "NO CLAUDE CODE INJECTION YET",
        ):
            assert label in result.stdout

    def test_json_mode_returns_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "patches", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        d = json.loads(result.stdout)
        assert "labels" in d
        for label in (
            "PREVIEW ONLY",
            "NOT APPLIED",
            "NO PROMPT MUTATION",
            "NO CLAUDE CODE INJECTION YET",
        ):
            assert label in d["labels"]


# ── 19. JSON shape ────────────────────────────────────────────────────


class TestJsonShape:

    def test_to_dict_round_trip(self):
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        payload = json.loads(json.dumps(result[0].to_dict()))
        assert payload["mode"] in PI_1_SUPPORTED_MODES
        assert payload["target"] == TARGET_COMPANION_CONTEXT
        assert payload["applied"] is False
        assert payload["source"] == SOURCE_PI


# ── 20. Mode validation (cross-cutting) ───────────────────────────────


class TestModeValidation:
    """Pin the PI-1 mode set + rejection of rewrite_prompt."""

    def test_supported_modes_pinned(self):
        assert PI_1_SUPPORTED_MODES == frozenset({
            MODE_PREVIEW_ONLY,
            MODE_INJECT_GUIDANCE,
            MODE_ASK_CLARIFICATION,
        })
        assert MODE_REWRITE_PROMPT not in PI_1_SUPPORTED_MODES
        assert MODE_REWRITE_PROMPT in ALL_MODES

    def test_rewrite_prompt_returns_zero_in_pi_1(self):
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(mode=MODE_REWRITE_PROMPT),
            ctx=_builder_ctx(),
        )
        assert result == ()


# ── 21. Telemetry round-trip (cross-cutting) ──────────────────────────


class TestTelemetryRoundTrip:

    def test_table_created_on_first_write(self, tmp_path: Path):
        store = IntentPatchStore(db_path=tmp_path / "t.db")
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        for p in result:
            store.write(IntentPatchRow(patch=p, created_at="t"))
        store.close()
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='intent_patches'"
        )
        assert cur.fetchone() is not None

    def test_fetch_latest_round_trip(self, tmp_path: Path):
        store = IntentPatchStore(db_path=tmp_path / "t.db")
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        for p in result:
            store.write(IntentPatchRow(patch=p, created_at="2026-04-26T20:00:00"))
        latest = store.fetch_latest()
        assert latest is not None
        assert latest["mode"] == MODE_INJECT_GUIDANCE
        assert latest["target"] == TARGET_COMPANION_CONTEXT
        assert latest["applied"] is False
        assert latest["source"] == SOURCE_PI

    def test_fetch_for_suggestion(self, tmp_path: Path):
        store = IntentPatchStore(db_path=tmp_path / "t.db")
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        for p in result:
            store.write(IntentPatchRow(patch=p, created_at="t"))
        rows = store.fetch_for_suggestion(suggestion.suggestion_id)
        assert len(rows) == 1

    def test_writer_does_not_raise_on_unwritable_path(self):
        bad = Path("/proc/this-cannot-be-a-real-file.db")
        store = IntentPatchStore(db_path=bad)
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            return
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        for p in result:
            store.write(IntentPatchRow(patch=p, created_at="t"))


# ── 22. Patch text length cap (cross-cutting) ─────────────────────────


class TestPatchTextLengthCap:

    def test_patch_text_length_within_cap(self):
        suggestion, contract, decision = _make_eligible_inputs(intent_class="create")
        if suggestion is None:
            pytest.skip("baseline suggestion not produced")
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=_pi(),
            ctx=_builder_ctx(),
        )
        for p in result:
            assert len(p.patch_text) <= PATCH_TEXT_MAX_LEN
