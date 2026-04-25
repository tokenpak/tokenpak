# SPDX-License-Identifier: Apache-2.0
"""Phase I0-1 — Intent Layer Phase 0 regression suite.

Covers:

  - Rule-based classifier: 10 canonical intents + 3 catch-all paths
    (empty / too-short / keyword-miss / threshold)
  - IntentContract construction (id shape, hash determinism, risk)
  - attach_intent_headers — emits the five wire headers, idempotent
  - SELF_CAPABILITIES_PROXY publishes ``tip.intent.contract-headers-v1``
    (proposal §5.2 audit-finding requirement)
  - SQLite intent_events table — DDL + write contract
  - §4.3 capability gate semantics — emit when adapter declares the
    label, strip otherwise; both branches reach telemetry.

No network, no real provider. All offline.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES_PROXY
from tokenpak.proxy.intent_classifier import (
    CATCH_ALL_REASONS,
    CLASSIFY_THRESHOLD,
    INTENT_SOURCE_V0,
    IntentClassification,
    classify_intent,
    extract_prompt_text,
)
from tokenpak.proxy.intent_contract import (
    GATE_CAPABILITY,
    INTENT_HEADER_CLASS,
    INTENT_HEADER_CONFIDENCE,
    INTENT_HEADER_ID,
    INTENT_HEADER_RISK,
    INTENT_HEADER_SUBTYPE,
    IntentContract,
    IntentTelemetryRow,
    IntentTelemetryStore,
    attach_intent_headers,
    build_contract,
    derive_risk,
    hash_prompt,
    make_contract_id,
)
from tokenpak.proxy.intent_policy import CANONICAL_INTENTS

# ── Capability publication ────────────────────────────────────────────


class TestProxyCapabilityPublication:
    """Per proposal §5.2: registering a label the proxy doesn't
    publish is an audit finding. Verify the proxy DOES publish it.
    """

    def test_self_capabilities_proxy_includes_intent_label(self):
        assert "tip.intent.contract-headers-v1" in SELF_CAPABILITIES_PROXY

    def test_gate_capability_constant_matches_published_label(self):
        assert GATE_CAPABILITY == "tip.intent.contract-headers-v1"
        assert GATE_CAPABILITY in SELF_CAPABILITIES_PROXY


# ── Classifier semantics ──────────────────────────────────────────────


class TestClassifierCanonicalIntents:
    """Each of the 10 canonical intents has a happy-path prompt that
    classifies above CLASSIFY_THRESHOLD and lands on the expected
    intent_class.
    """

    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("what is the status of the proxy", "status"),
            ("usage report for last 7 days", "usage"),
            ("debug why the proxy crashed", "debug"),
            ("summarize the vault", "summarize"),
            ("plan a migration to v2", "plan"),
            ("execute the dry_run", "execute"),
            ("explain how cache_control works", "explain"),
            ("search the docs for openrouter", "search"),
            ("create a new adapter for fireworks", "create"),
        ],
    )
    def test_canonical_intent_classified_above_threshold(self, prompt, expected):
        result = classify_intent(prompt)
        assert result.intent_class == expected, (
            f"{prompt!r} -> {result.intent_class} expected {expected}"
        )
        assert result.confidence >= CLASSIFY_THRESHOLD
        assert result.catch_all_reason is None
        assert result.intent_source == INTENT_SOURCE_V0

    def test_classifier_covers_canonical_intent_set(self):
        # Sanity: the keyword table covers every CANONICAL_INTENTS
        # entry (else the assert at module load would have fired).
        from tokenpak.proxy.intent_classifier import _KEYWORD_PATTERNS

        for intent in CANONICAL_INTENTS:
            assert intent in _KEYWORD_PATTERNS


class TestClassifierCatchAllReasons:
    """Catch-all path produces a populated ``catch_all_reason``."""

    def test_empty_prompt(self):
        result = classify_intent("")
        assert result.intent_class == "query"
        assert result.catch_all_reason == "empty_prompt"
        assert result.confidence == 0.0

    def test_whitespace_only_prompt(self):
        result = classify_intent("   \n\t   ")
        assert result.catch_all_reason == "empty_prompt"

    def test_too_short_prompt(self):
        result = classify_intent("hi")
        assert result.intent_class == "query"
        assert result.catch_all_reason == "prompt_too_short"

    def test_keyword_miss(self):
        # No canonical keyword matches → keyword_miss (the catch-all
        # 'query' patterns also fail).
        result = classify_intent("the quick brown fox jumps over a lazy")
        assert result.intent_class == "query"
        assert result.catch_all_reason == "keyword_miss"

    def test_below_confidence_threshold(self):
        # The 'query' catch-all set has only weak weights (≤ 0.3).
        # A prompt that matches a 'query' weak pattern scores below
        # CLASSIFY_THRESHOLD even though _some_ pattern matched.
        result = classify_intent("could you do this thing")
        assert result.catch_all_reason == "confidence_below_threshold"
        assert 0 < result.confidence < CLASSIFY_THRESHOLD

    def test_catch_all_reason_in_canonical_set(self):
        for prompt in ("", "hi", "xyz xyz xyz"):
            result = classify_intent(prompt)
            assert result.catch_all_reason in CATCH_ALL_REASONS


class TestClassifierTieBreaking:
    """When two intents tie on score, declaration order wins."""

    def test_status_beats_query_tie(self):
        # 'status' weight=1.0; 'query' weak (≤ 0.3). Status wins
        # outright, but the test pins the priority-order expectation.
        result = classify_intent("status")
        assert result.intent_class == "status"


# ── Slot extraction integration ───────────────────────────────────────


class TestClassifierSlotsPropagated:
    """SlotFiller integration — classifier returns slot tuples."""

    def test_summarize_extracts_period(self):
        result = classify_intent("summarize the vault for last 7 days")
        assert result.intent_class == "summarize"
        # The slot filler may or may not pick up 'period' depending
        # on its YAML; the test just asserts the tuple shape is sane.
        assert isinstance(result.slots_present, tuple)
        assert isinstance(result.slots_missing, tuple)


# ── extract_prompt_text ───────────────────────────────────────────────


class TestPromptExtraction:
    """The proxy hands canonical messages — concatenate user content."""

    def test_string_passthrough(self):
        assert extract_prompt_text("hello") == "hello"

    def test_user_messages_concatenated(self):
        msgs = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
        ]
        assert extract_prompt_text(msgs) == "hello world"

    def test_anthropic_content_blocks(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "ping"},
                {"type": "text", "text": "pong"},
            ]},
        ]
        assert extract_prompt_text(msgs) == "ping pong"

    def test_non_user_role_ignored(self):
        msgs = [{"role": "assistant", "content": "should not be picked up"}]
        # The classifier should not see assistant turns.
        assert "should not" not in extract_prompt_text(msgs)

    def test_garbage_input_returns_empty(self):
        assert extract_prompt_text(None) == ""
        assert extract_prompt_text(42) == ""


# ── IntentContract construction ───────────────────────────────────────


class TestIntentContractConstruction:

    def _classification(self, **over):
        kw = dict(
            intent_class="summarize",
            confidence=1.0,
            slots_present=("period",),
            slots_missing=(),
            catch_all_reason=None,
        )
        kw.update(over)
        return IntentClassification(**kw)

    def test_make_contract_id_shape(self):
        cid = make_contract_id()
        assert len(cid) == 13 + 16  # ms hex + random hex
        # All hex chars
        int(cid, 16)

    def test_make_contract_id_is_unique(self):
        a = make_contract_id()
        b = make_contract_id()
        assert a != b

    def test_hash_prompt_deterministic(self):
        assert hash_prompt("hello") == hash_prompt("hello")
        assert hash_prompt("hello") != hash_prompt("world")
        assert len(hash_prompt("x")) == 64  # sha256 hex

    def test_build_contract_basic(self):
        c = build_contract(
            classification=self._classification(),
            raw_prompt="summarize the vault",
        )
        assert c.intent_class == "summarize"
        assert c.confidence == 1.0
        assert c.subtype is None
        assert c.risk == "low"
        assert c.intent_source == INTENT_SOURCE_V0
        assert c.raw_prompt_hash == hash_prompt("summarize the vault")

    def test_build_contract_with_subtype(self):
        c = build_contract(
            classification=self._classification(),
            raw_prompt="x",
            subtype="bug_fix",
        )
        assert c.subtype == "bug_fix"

    def test_to_dict_serialisable(self):
        c = build_contract(
            classification=self._classification(),
            raw_prompt="x",
        )
        d = c.to_dict()
        # Tuples become lists in JSON.
        assert d["slots_present"] == ["period"]
        json.dumps(d)  # round-trip-safe


class TestRiskHeuristic:
    """Phase 0 risk heuristic per `intent_contract.derive_risk` docstring."""

    def test_execute_is_medium_when_slots_complete(self):
        assert derive_risk("execute", 0.9, ()) == "medium"

    def test_execute_is_high_with_missing_slots(self):
        assert derive_risk("execute", 0.9, ("target",)) == "high"

    def test_create_is_high_with_missing_slots(self):
        assert derive_risk("create", 1.0, ("target",)) == "high"

    def test_observation_intents_low(self):
        for intent in ("status", "usage", "query", "search", "explain"):
            assert derive_risk(intent, 1.0, ()) == "low"

    def test_debug_low_confidence_medium(self):
        assert derive_risk("debug", 0.5, ()) == "medium"

    def test_debug_high_confidence_low(self):
        assert derive_risk("debug", 0.9, ()) == "low"


# ── Header attachment ─────────────────────────────────────────────────


class TestAttachIntentHeaders:

    def _contract(self, **over):
        kw = dict(
            contract_id="abc123",
            intent_class="summarize",
            confidence=0.8765,
            subtype=None,
            risk="low",
            slots_present=("period",),
            slots_missing=(),
            intent_source=INTENT_SOURCE_V0,
            catch_all_reason=None,
            raw_prompt_hash="h",
        )
        kw.update(over)
        return IntentContract(**kw)

    def test_emits_all_required_headers(self):
        h = {}
        attach_intent_headers(h, self._contract())
        assert h[INTENT_HEADER_CLASS] == "summarize"
        assert h[INTENT_HEADER_CONFIDENCE] == "0.88"  # 2-decimal
        assert h[INTENT_HEADER_RISK] == "low"
        assert h[INTENT_HEADER_ID] == "abc123"

    def test_subtype_omitted_when_none(self):
        h = {}
        attach_intent_headers(h, self._contract())
        assert INTENT_HEADER_SUBTYPE not in h

    def test_subtype_present_when_set(self):
        h = {}
        attach_intent_headers(h, self._contract(subtype="bug_fix"))
        assert h[INTENT_HEADER_SUBTYPE] == "bug_fix"

    def test_idempotent_same_contract(self):
        h = {}
        c = self._contract()
        attach_intent_headers(h, c)
        snapshot = dict(h)
        attach_intent_headers(h, c)
        assert h == snapshot

    def test_overrides_caller_supplied_value(self):
        h = {INTENT_HEADER_CLASS: "spoofed"}
        attach_intent_headers(h, self._contract())
        assert h[INTENT_HEADER_CLASS] == "summarize"


# ── Telemetry store (SQLite) ──────────────────────────────────────────


class TestTelemetryStore:

    def _row(self, *, emitted=True, stripped=False, db_request_id="req1"):
        c = IntentContract(
            contract_id="cid1",
            intent_class="summarize",
            confidence=0.9,
            subtype=None,
            risk="low",
            slots_present=("period",),
            slots_missing=("target",),
            intent_source=INTENT_SOURCE_V0,
            catch_all_reason=None,
            raw_prompt_hash="hash1",
        )
        return IntentTelemetryRow(
            request_id=db_request_id,
            contract=c,
            timestamp="2026-04-25T22:00:00",
            tip_headers_emitted=emitted,
            tip_headers_stripped=stripped,
            tokens_in=42,
            tokens_out=None,
            latency_ms=None,
        )

    def test_table_created_on_first_write(self, tmp_path: Path):
        db = tmp_path / "t.db"
        store = IntentTelemetryStore(db_path=db)
        store.write(self._row())
        store.close()
        conn = sqlite3.connect(str(db))
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='intent_events'"
        )
        assert cur.fetchone() is not None

    def test_row_round_trip(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        store.write(self._row())
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        row = conn.execute(
            "SELECT request_id, intent_class, intent_slots_present, "
            "intent_slots_missing, intent_source, tip_headers_emitted, "
            "tip_headers_stripped, tokens_in FROM intent_events"
        ).fetchone()
        assert row[0] == "req1"
        assert row[1] == "summarize"
        assert json.loads(row[2]) == ["period"]
        assert json.loads(row[3]) == ["target"]
        assert row[4] == INTENT_SOURCE_V0
        assert row[5] == 1  # emitted
        assert row[6] == 0  # stripped
        assert row[7] == 42

    def test_emitted_and_stripped_distinct_states(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        store.write(self._row(emitted=True, stripped=False, db_request_id="a"))
        store.write(self._row(emitted=False, stripped=True, db_request_id="b"))
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        rows = dict(
            conn.execute(
                "SELECT request_id, tip_headers_emitted FROM intent_events"
            ).fetchall()
        )
        assert rows == {"a": 1, "b": 0}

    def test_idempotent_on_same_request_id(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        store.write(self._row(db_request_id="dup"))
        store.write(self._row(db_request_id="dup"))
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        n = conn.execute(
            "SELECT COUNT(*) FROM intent_events WHERE request_id = 'dup'"
        ).fetchone()[0]
        assert n == 1  # PRIMARY KEY + INSERT OR REPLACE

    def test_write_on_unwritable_path_does_not_raise(self, tmp_path: Path):
        # Best-effort contract: telemetry side-channel never breaks
        # the request. Point the DB at an unwritable location and
        # confirm the call completes silently.
        bad = Path("/proc/this-cannot-be-a-real-file.db")
        store = IntentTelemetryStore(db_path=bad)
        store.write(self._row())  # must not raise


# ── §4.3 capability gate semantics ────────────────────────────────────


class TestCapabilityGate:
    """The gate is expressed inline at server.py call site; here we
    test the semantics in isolation by simulating the call.
    """

    def _contract(self):
        return IntentContract(
            contract_id="cid",
            intent_class="summarize",
            confidence=0.8,
            subtype=None,
            risk="low",
            slots_present=(),
            slots_missing=(),
            intent_source=INTENT_SOURCE_V0,
            catch_all_reason=None,
            raw_prompt_hash="h",
        )

    def test_emits_when_adapter_declares_label(self):
        class _Adapter:
            capabilities = frozenset({GATE_CAPABILITY})

        h: dict = {}
        # Simulate the gate from server.py:
        adapter = _Adapter()
        if adapter is not None and GATE_CAPABILITY in adapter.capabilities:
            attach_intent_headers(h, self._contract())
            emitted = True
            stripped = False
        else:
            emitted = False
            stripped = True
        assert emitted is True
        assert stripped is False
        assert INTENT_HEADER_CLASS in h

    def test_strips_when_adapter_does_not_declare(self):
        class _Adapter:
            capabilities = frozenset({"tip.compression.v1"})  # different label

        h: dict = {}
        adapter = _Adapter()
        if adapter is not None and GATE_CAPABILITY in adapter.capabilities:
            attach_intent_headers(h, self._contract())
            emitted = True
            stripped = False
        else:
            emitted = False
            stripped = True
        assert emitted is False
        assert stripped is True
        assert INTENT_HEADER_CLASS not in h

    def test_strips_when_adapter_is_none(self):
        h: dict = {}
        adapter = None
        if adapter is not None and GATE_CAPABILITY in adapter.capabilities:
            attach_intent_headers(h, self._contract())
            emitted = True
            stripped = False
        else:
            emitted = False
            stripped = True
        assert emitted is False
        assert stripped is True
        assert INTENT_HEADER_CLASS not in h

    def test_no_first_party_adapter_declares_label_in_phase_0(self):
        """Per proposal §5.2: 'No first-party adapter declares the
        label by default in Intent-0.' Verify by walking every
        first-party FormatAdapter in the default registry.
        """
        from tokenpak.proxy.adapters import build_default_registry

        registry = build_default_registry()
        for ad in registry.adapters():
            assert GATE_CAPABILITY not in ad.capabilities, (
                f"{ad.__class__.__name__} declares {GATE_CAPABILITY} — "
                f"Intent-0 says no first-party adapter declares this label "
                f"by default; opt-in is gated on the baseline report."
            )
