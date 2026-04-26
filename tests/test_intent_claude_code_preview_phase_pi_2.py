# SPDX-License-Identifier: Apache-2.0
"""Phase PI-2 — Claude Code Companion preview surface tests.

Fourteen directive-mandated test categories:

  1. read model — empty DB
  2. read model — populated DB (full chain)
  3. CLI human output
  4. CLI JSON output
  5. source_client vs format_adapter distinction
  6. patch preview labels present
  7. forbidden wording guardrail still enforced
  8. no raw prompt text emitted
  9. no secrets emitted
  10. no request mutation
  11. no route mutation
  12. no classifier mutation
  13. no TIP header emission
  14. (optional MCP — deferred per directive; no MCP test in PI-2)

Plus the simulated Claude-Code-style smoke test from §5 of the
directive: builds a /v1/messages-shaped request through the
production engine + suggestion + patch builder; verifies all
four tables; verifies preview surface labels each layer
correctly.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_claude_code_preview import (
    CREDENTIAL_PROVIDER_CLAUDE_CODE,
    FORMAT_ADAPTER_ANTHROPIC,
    PREVIEW_LABELS,
    SOURCE_CLIENT_CLAUDE_CODE,
    WIRE_EMISSION_TELEMETRY_ONLY,
    collect_latest_patch_preview,
    collect_latest_preview,
)
from tokenpak.proxy.intent_contract import (
    IntentTelemetryRow,
    IntentTelemetryStore,
    build_contract,
)
from tokenpak.proxy.intent_policy_engine import (
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
)
from tokenpak.proxy.intent_policy_telemetry import (
    IntentPolicyDecisionRow,
    IntentPolicyDecisionStore,
)
from tokenpak.proxy.intent_prompt_patch import (
    FORBIDDEN_PHRASES,
    MODE_INJECT_GUIDANCE,
    TARGET_COMPANION_CONTEXT,
    PromptInterventionConfig,
    PromptPatchBuilderContext,
    build_patches,
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


def _seed_full_chain(*, db: Path,
                     intent_class="create",
                     prompt="create a new file",
                     timestamp=None,
                     pi_enabled=True,
                     pi_mode=MODE_INJECT_GUIDANCE):
    """Seed event + decision + suggestion + patch under one DB.

    Mirrors a single Claude-Code-shaped request flow end-to-end
    (Phase 0 → 2.1 → 2.4.1 → PI-1) without spinning up the proxy.
    Returns the four objects.
    """
    cls = IntentClassification(
        intent_class=intent_class, confidence=0.9,
        slots_present=("target",), slots_missing=(),
        catch_all_reason=None,
    )
    contract = build_contract(classification=cls, raw_prompt=prompt)
    ts = timestamp or _dt.datetime.now().isoformat(timespec="seconds")

    # Phase 0 row
    events = IntentTelemetryStore(db_path=db)
    events.write(IntentTelemetryRow(
        request_id="cc-r1", contract=contract, timestamp=ts,
        tip_headers_emitted=False, tip_headers_stripped=True,
    ))
    events.close()

    # Phase 2.1 row
    inp = PolicyInput(
        intent_class=intent_class, confidence=0.9,
        slots_present=("target",), slots_missing=(),
        catch_all_reason=None,
        provider="tokenpak-claude-code", model="claude-3-5-sonnet",
        live_verified_status=True,
    )
    decision = evaluate_policy(inp, PolicyEngineConfig())
    pstore = IntentPolicyDecisionStore(db_path=db)
    pstore.write(IntentPolicyDecisionRow(
        request_id="cc-r1",
        contract_id=contract.contract_id,
        timestamp=ts,
        decision=decision,
    ))
    pstore.close()

    # Phase 2.4.1 row
    sugg_ctx = SuggestionBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=frozenset({"tip.compression.v1"}),
    )
    suggestions = build_suggestions(
        decision=decision, contract=contract, ctx=sugg_ctx,
    )
    suggestion = suggestions[0] if suggestions else None
    if suggestion is not None:
        from tokenpak.proxy.intent_suggestion_telemetry import (
            IntentSuggestionRow,
            IntentSuggestionStore,
        )
        sstore = IntentSuggestionStore(db_path=db)
        sstore.write(IntentSuggestionRow(suggestion=suggestion, timestamp=ts))
        sstore.close()

    # PI-1 row
    patch = None
    if suggestion is not None:
        ctx = PromptPatchBuilderContext(
            config=PolicyEngineConfig(),
            adapter_capabilities=frozenset({
                "tip.compression.v1",
                "tip.byte-preserved-passthrough",
            }),
            required_slots=(),
        )
        result = build_patches(
            suggestion=suggestion, contract=contract, decision=decision,
            pi_config=PromptInterventionConfig(
                enabled=pi_enabled, mode=pi_mode,
                target=TARGET_COMPANION_CONTEXT,
            ),
            ctx=ctx,
        )
        if result:
            patch = result[0]
            pstore2 = IntentPatchStore(db_path=db)
            pstore2.write(IntentPatchRow(patch=patch, created_at=ts))
            pstore2.close()

    return contract, decision, suggestion, patch


# ── 1. Read model — empty DB ──────────────────────────────────────────


class TestReadModelEmptyDB:

    def test_collect_latest_preview_returns_none(self, tmp_path: Path):
        assert collect_latest_preview(db_path=tmp_path / "nope.db") is None

    def test_collect_latest_patch_preview_returns_none(self, tmp_path: Path):
        assert collect_latest_patch_preview(db_path=tmp_path / "nope.db") is None

    def test_table_missing_returns_none(self, tmp_path: Path):
        # Empty file, no schema.
        db = tmp_path / "telemetry.db"
        sqlite3.connect(str(db)).close()
        assert collect_latest_preview(db_path=db) is None


# ── 2. Read model — populated DB (full chain) ─────────────────────────


class TestReadModelPopulatedDB:

    def test_full_chain_present(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        contract, decision, suggestion, patch = _seed_full_chain(db=db)
        assert suggestion is not None
        assert patch is not None

        payload = collect_latest_preview(db_path=db)
        assert payload is not None
        # Identity-separation labels.
        assert payload["source_client"] == SOURCE_CLIENT_CLAUDE_CODE
        assert payload["format_adapter"] == FORMAT_ADAPTER_ANTHROPIC
        assert payload["credential_provider"] == CREDENTIAL_PROVIDER_CLAUDE_CODE
        assert payload["wire_emission"] == WIRE_EMISSION_TELEMETRY_ONLY
        # Section payloads.
        assert payload["event"]["contract_id"] == contract.contract_id
        assert payload["decision"]["decision_id"] == decision.decision_id
        assert payload["suggestion"]["suggestion_id"] == suggestion.suggestion_id
        assert payload["patch"]["patch_id"] == patch.patch_id
        # applied is always False.
        assert payload["patch"]["applied"] is False

    def test_event_only_chain(self, tmp_path: Path):
        # Only Phase 0 row; no decision / suggestion / patch.
        db = tmp_path / "telemetry.db"
        cls = IntentClassification(
            intent_class="status", confidence=1.0,
            slots_present=(), slots_missing=(),
            catch_all_reason=None,
        )
        contract = build_contract(classification=cls, raw_prompt="status")
        events = IntentTelemetryStore(db_path=db)
        events.write(IntentTelemetryRow(
            request_id="solo", contract=contract,
            timestamp="2026-04-26T12:00:00",
            tip_headers_emitted=False, tip_headers_stripped=True,
        ))
        events.close()

        payload = collect_latest_preview(db_path=db)
        assert payload is not None
        assert payload["event"]["contract_id"] == contract.contract_id
        assert payload["decision"] is None
        assert payload["suggestion"] is None
        assert payload["patch"] is None


# ── 3. CLI human output ───────────────────────────────────────────────


class TestCliHumanOutput:

    def test_intent_runs_clean(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "intent"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        # All preview labels appear in the human output.
        for label in PREVIEW_LABELS:
            assert label in result.stdout, (
                f"missing preview label {label!r} in human output"
            )

    def test_patches_runs_clean(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        for label in PREVIEW_LABELS:
            assert label in result.stdout

    def test_help_includes_intent_and_patches(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "intent" in result.stdout
        assert "patches" in result.stdout


# ── 4. CLI JSON output ────────────────────────────────────────────────


class TestCliJsonOutput:

    def test_intent_json_parses(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "intent", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        d = json.loads(result.stdout)
        assert "labels" in d
        for label in PREVIEW_LABELS:
            assert label in d["labels"]

    def test_patches_json_parses(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        d = json.loads(result.stdout)
        assert "labels" in d
        for label in PREVIEW_LABELS:
            assert label in d["labels"]


# ── 5. source_client vs format_adapter distinction ────────────────────


class TestIdentitySeparation:

    def test_constants_pinned(self):
        # Critical invariant per directive § 3 — no fake
        # ClaudeCodeAdapter.
        assert SOURCE_CLIENT_CLAUDE_CODE == "claude_code"
        assert FORMAT_ADAPTER_ANTHROPIC == "AnthropicAdapter"
        assert CREDENTIAL_PROVIDER_CLAUDE_CODE == "tokenpak-claude-code"
        assert WIRE_EMISSION_TELEMETRY_ONLY == "telemetry_only"

    def test_no_claude_code_adapter_class_exists(self):
        # Defensive: if a future code path adds a fake
        # ClaudeCodeAdapter, this test trips.
        from tokenpak.proxy.adapters import build_default_registry

        for ad in build_default_registry().adapters():
            assert ad.__class__.__name__ != "ClaudeCodeAdapter", (
                f"unexpected adapter class {ad.__class__.__name__!r}; "
                f"PI-2 directive § 3 forbids fake ClaudeCodeAdapter"
            )

    def test_claude_code_credential_provider_exists_separately(self):
        # The credential provider IS the Claude Code identity layer;
        # the format adapter is AnthropicAdapter. Both must exist.
        from tokenpak.services.routing_service.credential_injector import (
            ClaudeCodeCredentialProvider,
        )
        assert ClaudeCodeCredentialProvider.name == "tokenpak-claude-code"
        # And it does not have a `capabilities` attribute (it's
        # not a FormatAdapter).
        assert not hasattr(ClaudeCodeCredentialProvider, "source_format")


# ── 6. Patch preview labels present ───────────────────────────────────


class TestPreviewLabelsPresent:

    REQUIRED_LABELS = {
        "Claude Code Companion Intent Preview",
        "PREVIEW ONLY",
        "NOT APPLIED",
        "NO PROMPT MUTATION",
        "NO CLAUDE CODE INJECTION YET",
        "Telemetry-only; no TIP intent headers emitted",
    }

    def test_all_six_labels_pinned(self):
        assert self.REQUIRED_LABELS == set(PREVIEW_LABELS)

    def test_labels_appear_in_payload(self, tmp_path: Path):
        _seed_full_chain(db=tmp_path / "telemetry.db")
        payload = collect_latest_preview(db_path=tmp_path / "telemetry.db")
        assert payload is not None
        for label in self.REQUIRED_LABELS:
            assert label in payload["labels"]


# ── 7. Forbidden wording guardrail still enforced ─────────────────────


class TestForbiddenWordingGuardrail:

    def test_no_forbidden_phrase_in_payload(self, tmp_path: Path):
        # Run the full chain; assert no patch_text in the payload
        # contains any forbidden phrase.
        _seed_full_chain(db=tmp_path / "telemetry.db", intent_class="create")
        payload = collect_latest_preview(db_path=tmp_path / "telemetry.db")
        if payload is None or payload["patch"] is None:
            pytest.skip("no patch row produced in this seed")
        forbidden_re = re.compile(
            r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
            re.IGNORECASE,
        )
        for field in ("patch_text", "reason"):
            v = payload["patch"].get(field, "")
            m = forbidden_re.search(v)
            assert m is None, (
                f"forbidden phrase {m.group(0)!r} in patch.{field}"
            )


# ── 8 + 9. No raw prompt / secrets emitted ────────────────────────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PI-2"

    def test_sentinel_absent_from_payload(self, tmp_path: Path):
        _seed_full_chain(
            db=tmp_path / "telemetry.db",
            intent_class="create",
            prompt=f"create a new file {self.SENTINEL}",
        )
        payload = collect_latest_preview(db_path=tmp_path / "telemetry.db")
        assert self.SENTINEL not in json.dumps(payload, default=str)

    def test_sentinel_absent_from_patch_preview(self, tmp_path: Path):
        _seed_full_chain(
            db=tmp_path / "telemetry.db",
            intent_class="create",
            prompt=f"create a new file {self.SENTINEL}",
        )
        payload = collect_latest_patch_preview(db_path=tmp_path / "telemetry.db")
        if payload is None:
            pytest.skip("no patch row produced")
        assert self.SENTINEL not in json.dumps(payload, default=str)


# ── 10. No request mutation ───────────────────────────────────────────


class TestNoRequestMutation:

    def test_repeated_reads_do_not_mutate_db(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_full_chain(db=db)
        before_size = db.stat().st_size
        before_mtime = db.stat().st_mtime
        for _ in range(5):
            collect_latest_preview(db_path=db)
            collect_latest_patch_preview(db_path=db)
        assert db.stat().st_size == before_size
        assert db.stat().st_mtime == before_mtime


# ── 11. No route mutation ─────────────────────────────────────────────


class TestNoRouteMutation:

    def test_module_does_not_import_dispatch(self):
        import tokenpak.proxy.intent_claude_code_preview as m
        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src


# ── 12. No classifier mutation ────────────────────────────────────────


class TestNoClassifierMutation:

    def test_classifier_constants_unchanged(self):
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )
        assert CLASSIFY_THRESHOLD == 0.4
        assert INTENT_SOURCE_V0 == "rule_based_v0"


# ── 13. No TIP header emission ────────────────────────────────────────


class TestNoTipHeaderEmission:

    def test_payload_pins_telemetry_only_wire_emission(self, tmp_path: Path):
        _seed_full_chain(db=tmp_path / "telemetry.db")
        payload = collect_latest_preview(db_path=tmp_path / "telemetry.db")
        assert payload is not None
        assert payload["wire_emission"] == "telemetry_only"
        # The Phase 0 event row's tip_headers_emitted bit MUST be
        # False (the seed sets it; AnthropicAdapter doesn't declare
        # the gate; this is the default-off invariant from PR #44).
        assert payload["event"]["tip_headers_emitted"] is False
        assert payload["event"]["tip_headers_stripped"] is True


# ── §5 directive: simulated Claude-Code-style smoke test ──────────────


class TestSimulatedClaudeCodeSmoke:
    """Simulates a /v1/messages request shape (Claude Code's
    canonical Anthropic Messages path) end-to-end through the
    Phase 0 → 2.1 → 2.4.1 → PI-1 pipeline (without spinning up
    the HTTP proxy). Verifies every layer landed.
    """

    def test_full_pipeline_creates_all_four_rows(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        contract, decision, suggestion, patch = _seed_full_chain(
            db=db,
            intent_class="create",
            prompt="create a new test file in tests/ that imports the module",
        )
        # All four layers exist.
        assert contract is not None
        assert decision is not None
        # Engine emits suggest_compression_profile for create →
        # eligible suggestion.
        assert suggestion is not None
        # PI-1 builds a patch when prompt_intervention.enabled.
        assert patch is not None

        # Verify each row is in its own table.
        conn = sqlite3.connect(str(db))
        for tbl in (
            "intent_events",
            "intent_policy_decisions",
            "intent_suggestions",
            "intent_patches",
        ):
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            assert n == 1, f"{tbl} has {n} rows; expected 1"
        conn.close()

        # Run the read model.
        payload = collect_latest_preview(db_path=db)
        assert payload is not None
        # Identity-separation labels.
        assert payload["source_client"] == "claude_code"
        assert payload["format_adapter"] == "AnthropicAdapter"
        assert payload["wire_emission"] == "telemetry_only"
        # applied always False.
        assert payload["patch"]["applied"] is False
        # No TIP headers emitted.
        assert payload["event"]["tip_headers_emitted"] is False


# ── Cross-cutting: read model returns None when telemetry empty ───────


class TestReadModelGracefulDegradation:

    def test_read_model_does_not_raise_on_partial_chain(self, tmp_path: Path):
        # Phase 0 only; no decision/suggestion/patch rows.
        db = tmp_path / "telemetry.db"
        cls = IntentClassification(
            intent_class="status", confidence=1.0,
            slots_present=(), slots_missing=(),
            catch_all_reason=None,
        )
        contract = build_contract(classification=cls, raw_prompt="status")
        events = IntentTelemetryStore(db_path=db)
        events.write(IntentTelemetryRow(
            request_id="solo", contract=contract,
            timestamp="2026-04-26T12:00:00",
            tip_headers_emitted=False, tip_headers_stripped=True,
        ))
        events.close()

        payload = collect_latest_preview(db_path=db)
        assert payload is not None
        assert payload["decision"] is None
        assert payload["suggestion"] is None
        assert payload["patch"] is None
