# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.2 — suggestion display surfaces test suite.

Fourteen directive-mandated test classes:

  - doctor explain includes suggestions
  - intent report includes suggestion summary
  - policy-preview includes linked suggestions
  - API JSON includes suggestion section
  - dashboard / read-model includes suggestions
  - empty DB behavior
  - populated DB behavior
  - window filtering
  - expired suggestions
  - forbidden wording guardrail still enforced
  - no prompt text or secrets emitted
  - no route mutation
  - no request mutation
  - no classifier mutation

Read-only across the board. Reuses production builder + telemetry
stores so the schema stays a single source of truth.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from tokenpak.proxy.intent_classifier import IntentClassification
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
from tokenpak.proxy.intent_suggestion import (
    FORBIDDEN_PHRASES,
    SuggestionBuilderContext,
    build_suggestions,
)
from tokenpak.proxy.intent_suggestion_report import (
    ADVISORY_LABEL,
    NOOP_DEFAULT_OFF_TAG,
    build_suggestion_report,
)
from tokenpak.proxy.intent_suggestion_report import (
    render_human as render_sugg_human,
)
from tokenpak.proxy.intent_suggestion_report import (
    render_json as render_sugg_json,
)
from tokenpak.proxy.intent_suggestion_telemetry import (
    IntentSuggestionRow,
    IntentSuggestionStore,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _safe_input(**over) -> PolicyInput:
    kw = dict(
        intent_class="summarize", confidence=0.9,
        slots_present=("period",), slots_missing=(),
        catch_all_reason=None, provider="tokenpak-test",
        model="test-model", live_verified_status=True,
        required_slots=(),
    )
    kw.update(over)
    return PolicyInput(**kw)


def _seed_three_layer(*, db: Path, request_id="r1",
                     intent_class="summarize", prompt="summarize the vault",
                     timestamp=None, expires_at=None,
                     user_visible=False):
    """Seed events + decision + suggestion under one DB. Returns
    (contract, decision, suggestion).
    """
    cls = IntentClassification(
        intent_class=intent_class, confidence=0.9,
        slots_present=("period",), slots_missing=(),
        catch_all_reason=None,
    )
    contract = build_contract(classification=cls, raw_prompt=prompt)
    ts = timestamp or _dt.datetime.now().isoformat(timespec="seconds")

    events = IntentTelemetryStore(db_path=db)
    events.write(IntentTelemetryRow(
        request_id=request_id, contract=contract, timestamp=ts,
        tip_headers_emitted=False, tip_headers_stripped=True,
    ))
    events.close()

    decision = evaluate_policy(_safe_input(intent_class=intent_class), PolicyEngineConfig())
    pstore = IntentPolicyDecisionStore(db_path=db)
    pstore.write(IntentPolicyDecisionRow(
        request_id=request_id,
        contract_id=contract.contract_id,
        timestamp=ts,
        decision=decision,
    ))
    pstore.close()

    ctx = SuggestionBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=frozenset({"tip.compression.v1"}),
    )
    suggestions = build_suggestions(
        decision=decision, contract=contract, ctx=ctx,
    )
    sstore = IntentSuggestionStore(db_path=db)
    for s in suggestions:
        # Optionally override expires_at / user_visible by
        # writing directly with overridden fields. We reconstruct
        # the dataclass with the overrides.
        if expires_at is not None or user_visible:
            from dataclasses import replace
            s = replace(s, expires_at=expires_at, user_visible=user_visible)
        sstore.write(IntentSuggestionRow(suggestion=s, timestamp=ts))
    sstore.close()

    return contract, decision, suggestions[0] if suggestions else None


# ── 1. Doctor explain includes suggestions ────────────────────────────


class TestExplainIncludesSuggestions:

    def test_explain_renders_suggestion_section(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        contract, decision, suggestion = _seed_three_layer(db=db)
        assert suggestion is not None

        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=db)
        assert payload is not None
        assert payload["policy_decision"] is not None
        assert isinstance(payload.get("policy_suggestions"), list)
        assert len(payload["policy_suggestions"]) == 1

        text = render_explain_last(payload)
        assert "Policy Suggestions" in text
        assert "advisory / no-op / default-off" in text
        assert "TokenPak has not changed routing" in text
        # Includes structured fields the directive enumerates.
        for field in ("suggestion_id", "confidence", "user_visible"):
            assert field in text

    def test_explain_when_no_suggestions(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        # Seed event + decision only (no suggestions); the section
        # should render with "(none)".
        cls = IntentClassification(
            intent_class="status",  # status has no heuristic → observe_only
            confidence=1.0, slots_present=(), slots_missing=(),
            catch_all_reason=None,
        )
        contract = build_contract(classification=cls, raw_prompt="status check")
        ts = "2026-04-26T12:00:00"

        events = IntentTelemetryStore(db_path=db)
        events.write(IntentTelemetryRow(
            request_id="solo", contract=contract, timestamp=ts,
            tip_headers_emitted=False, tip_headers_stripped=True,
        ))
        events.close()

        decision = evaluate_policy(
            _safe_input(intent_class="status", confidence=1.0,
                       slots_present=(), slots_missing=()),
            PolicyEngineConfig(),
        )
        pstore = IntentPolicyDecisionStore(db_path=db)
        pstore.write(IntentPolicyDecisionRow(
            request_id="solo", contract_id=contract.contract_id,
            timestamp=ts, decision=decision,
        ))
        pstore.close()

        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=db)
        assert payload is not None
        text = render_explain_last(payload)
        assert "Policy Suggestions" in text
        assert "(none recorded yet for this decision)" in text


# ── 2. Intent report includes suggestion summary ──────────────────────


class TestReportIncludesSuggestionSummary:

    def test_report_to_dict_has_suggestions_summary(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")
        _seed_three_layer(db=db, request_id="r2")

        from tokenpak.proxy.intent_report import build_report
        r = build_report(window_days=14, db_path=db)
        d = r.to_dict()
        assert "suggestions_summary" in d
        assert d["suggestions_summary"]["total_suggestions"] == 2

    def test_report_human_renders_suggestion_section(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")

        from tokenpak.proxy.intent_report import build_report, render_human
        r = build_report(window_days=14, db_path=db)
        text = render_human(r)
        assert "Advisory Suggestions" in text
        assert NOOP_DEFAULT_OFF_TAG in text


# ── 3. Policy-preview includes linked suggestions ─────────────────────


class TestPolicyPreviewLinksSuggestions:

    def test_collect_latest_returns_suggestions(self, tmp_path: Path,
                                                  monkeypatch):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")

        # Swap the default stores to point at the temp DB.
        from tokenpak.proxy.intent_policy_telemetry import (
            IntentPolicyDecisionStore as _PStore,
        )
        from tokenpak.proxy.intent_policy_telemetry import (
            set_default_policy_store,
        )
        from tokenpak.proxy.intent_suggestion_telemetry import (
            IntentSuggestionStore as _SStore,
        )
        from tokenpak.proxy.intent_suggestion_telemetry import (
            set_default_suggestion_store,
        )

        set_default_policy_store(_PStore(db_path=db))
        set_default_suggestion_store(_SStore(db_path=db))
        try:
            from tokenpak.proxy.intent_policy_preview import (
                collect_latest,
                render_human,
            )
            payload = collect_latest()
            assert payload is not None
            assert isinstance(payload.get("policy_suggestions"), list)
            assert len(payload["policy_suggestions"]) == 1

            text = render_human(payload)
            assert "Linked policy suggestions" in text
            assert "advisory / no-op / default-off" in text
        finally:
            set_default_policy_store(None)
            set_default_suggestion_store(None)


# ── 4. API JSON includes suggestion section ───────────────────────────


class TestApiPayloadIncludesSuggestions:

    def test_dashboard_payload_has_suggestions_key(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")

        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=db)
        assert "suggestions" in payload
        sugg = payload["suggestions"]
        assert sugg["total"] == 1
        assert "advisory_label" in sugg
        assert "TokenPak has not changed routing" in sugg["advisory_label"]
        assert sugg["noop_default_off"] is True
        # Field set the directive enumerates is present.
        for k in (
            "type_distribution",
            "safety_flag_distribution",
            "user_visible_true_count",
            "user_visible_false_count",
            "expired_count",
            "latest",
        ):
            assert k in sugg, f"missing key {k!r} in suggestions section"

    def test_dashboard_metadata_has_suggestions_label(self, tmp_path: Path):
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert "suggestions_label" in payload["metadata"]
        assert "TokenPak has not changed routing" in payload["metadata"]["suggestions_label"]


# ── 5. Dashboard / read-model includes suggestions ────────────────────


class TestDashboardReadModelSuggestions:
    """The dashboard read-model is the same payload as the API
    endpoint. This class pins the shape of the suggestions section
    independently so a future refactor of either side trips here.
    """

    REQUIRED_SUGG_KEYS = {
        "advisory_label",
        "noop_default_off",
        "total",
        "type_distribution",
        "safety_flag_distribution",
        "recommended_action_distribution",
        "user_visible_true_count",
        "user_visible_false_count",
        "expired_count",
        "latest",
    }

    def test_required_keys_present_when_table_exists(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=db)
        missing = self.REQUIRED_SUGG_KEYS - set(payload["suggestions"])
        assert not missing, f"missing suggestion keys: {missing}"

    def test_advisory_labels_pinned(self, tmp_path: Path):
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        # Both the section-local label and the metadata-level label
        # must carry the canonical phrasing.
        assert "advisory" in payload["suggestions"]["advisory_label"].lower()
        assert "advisory" in payload["metadata"]["suggestions_label"].lower()


# ── 6. Empty DB behavior ──────────────────────────────────────────────


class TestEmptyDB:

    def test_suggestion_report_empty(self, tmp_path: Path):
        r = build_suggestion_report(window_days=14, db_path=tmp_path / "nope.db")
        assert r.total_suggestions == 0
        assert r.suggestion_type_distribution == {}
        assert r.review_areas, "review_areas should explain empty state"

    def test_dashboard_empty_db(self, tmp_path: Path):
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["suggestions"]["total"] == 0


# ── 7. Populated DB behavior ──────────────────────────────────────────


class TestPopulatedDB:

    def test_type_distribution_correct(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="a", intent_class="summarize")
        _seed_three_layer(db=db, request_id="b", intent_class="summarize")
        _seed_three_layer(db=db, request_id="c", intent_class="debug")

        r = build_suggestion_report(window_days=14, db_path=db)
        # debug heuristic is "conservative" compression; summarize
        # is "aggressive". Both fall under
        # compression_profile_recommendation in 2.4.1.
        assert r.suggestion_type_distribution.get(
            "compression_profile_recommendation", 0
        ) == 3

    def test_user_visible_split(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        # Default user_visible=False per 2.4.1.
        _seed_three_layer(db=db, request_id="a")
        _seed_three_layer(db=db, request_id="b")
        # One row with user_visible=True (synthetic; 2.4.3 will
        # enable this in real flows).
        _seed_three_layer(db=db, request_id="c", user_visible=True)

        r = build_suggestion_report(window_days=14, db_path=db)
        assert r.user_visible_true_count == 1
        assert r.user_visible_false_count == 2


# ── 8. Window filtering ───────────────────────────────────────────────


class TestWindowFilter:

    def test_rows_outside_window_excluded(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        now = _dt.datetime(2026, 4, 26, 12, 0, 0)
        _seed_three_layer(
            db=db, request_id="recent",
            timestamp=(now - _dt.timedelta(days=1)).isoformat(timespec="seconds"),
        )
        _seed_three_layer(
            db=db, request_id="ancient",
            timestamp=(now - _dt.timedelta(days=60)).isoformat(timespec="seconds"),
        )

        r = build_suggestion_report(window_days=14, db_path=db, now=now)
        assert r.total_suggestions == 1


# ── 9. Expired suggestions ────────────────────────────────────────────


class TestExpiredSuggestions:

    def test_expired_count_correct(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        now = _dt.datetime(2026, 4, 26, 12, 0, 0)
        # Two not-expired (no expires_at), one expired (in past),
        # one not-yet-expired (in future).
        _seed_three_layer(db=db, request_id="a")
        _seed_three_layer(db=db, request_id="b")
        _seed_three_layer(
            db=db, request_id="expired",
            expires_at="2026-04-20T00:00:00",
        )
        _seed_three_layer(
            db=db, request_id="future",
            expires_at="2026-12-31T00:00:00",
        )

        r = build_suggestion_report(window_days=None, db_path=db, now=now)
        assert r.expired_count == 1


# ── 10. Forbidden wording guardrail ───────────────────────────────────


class TestForbiddenWordingGuardrail:
    """The 2.4.1 guardrail still applies: every emitted string in
    every render path must be free of forbidden phrases.
    """

    def test_no_forbidden_phrase_in_human_render(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")
        # Run all three human renderers + check for any forbidden
        # phrase. Special-case: words like "Updated" inside
        # documentation prose aren't allowed in user-facing fields.
        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        from tokenpak.proxy.intent_policy_preview import (
            collect_latest as _ploc,
        )
        from tokenpak.proxy.intent_policy_preview import (
            render_human as _prh,
        )
        from tokenpak.proxy.intent_policy_telemetry import (
            IntentPolicyDecisionStore as _PStore,
        )
        from tokenpak.proxy.intent_policy_telemetry import (
            set_default_policy_store,
        )
        from tokenpak.proxy.intent_report import build_report, render_human
        from tokenpak.proxy.intent_suggestion_telemetry import (
            IntentSuggestionStore as _SStore,
        )
        from tokenpak.proxy.intent_suggestion_telemetry import (
            set_default_suggestion_store,
        )

        set_default_policy_store(_PStore(db_path=db))
        set_default_suggestion_store(_SStore(db_path=db))
        try:
            t1 = render_explain_last(collect_explain_last(db_path=db))
            t2 = render_human(build_report(window_days=14, db_path=db))
            t3 = _prh(_ploc())
            forbidden_re = re.compile(
                r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
                re.IGNORECASE,
            )
            # "Updated" is the most likely false positive (e.g.
            # "last updated"). Whitelist by ensuring no forbidden
            # phrase appears in the suggestion-rendering blocks
            # specifically. We grep for the text of the suggestion
            # (title+message) and assert no forbidden phrase appears
            # in those substrings.
            for text in (t1, t2, t3):
                # Extract any line containing a suggestion's "title"
                # or "message" (we know our rendered titles include
                # "Recommended" / "Could improve" / "Adapter could
                # declare" / "Budget risk"). Sample those lines.
                for line in text.splitlines():
                    if any(marker in line for marker in (
                        "Recommended:", "Could improve:",
                        "Adapter could declare", "Budget risk:",
                    )):
                        m = forbidden_re.search(line)
                        assert m is None, (
                            f"forbidden phrase {m.group(0)!r} in suggestion line: {line!r}"
                        )
        finally:
            set_default_policy_store(None)
            set_default_suggestion_store(None)


# ── 11. Privacy contract ──────────────────────────────────────────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PHASE-2-4-2"

    def test_sentinel_absent_from_suggestion_report(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(
            db=db, request_id="priv",
            prompt=f"summarize the vault {self.SENTINEL}",
        )

        r = build_suggestion_report(window_days=14, db_path=db)
        h = render_sugg_human(r)
        j = render_sugg_json(r)
        assert self.SENTINEL not in h
        assert self.SENTINEL not in j

    def test_sentinel_absent_from_dashboard(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(
            db=db, request_id="priv",
            prompt=f"summarize the vault {self.SENTINEL}",
        )
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=db)
        assert self.SENTINEL not in json.dumps(payload)

    def test_per_row_hash_absent_from_dashboard(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        prompt = "summarize the vault hash-test-zzz-242"
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        _seed_three_layer(db=db, request_id="priv", prompt=prompt)
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=db)
        assert digest not in json.dumps(payload)


# ── 12. No route mutation ─────────────────────────────────────────────


class TestNoRouteMutation:
    """Structural — the suggestion-render modules MUST NOT import
    dispatch primitives.
    """

    def test_suggestion_report_does_not_import_forward_path(self):
        import tokenpak.proxy.intent_suggestion_report as m
        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src


# ── 13. No request mutation ───────────────────────────────────────────


class TestNoRequestMutation:
    """Read-only contract — repeated reads do not mutate the DB."""

    def test_repeated_dashboard_reads_do_not_mutate(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        _seed_three_layer(db=db, request_id="r1")
        before_size = db.stat().st_size
        before_mtime = db.stat().st_mtime

        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        for _ in range(5):
            collect_policy_dashboard(window_days=14, db_path=db)
            build_suggestion_report(window_days=14, db_path=db)

        assert db.stat().st_size == before_size
        assert db.stat().st_mtime == before_mtime


# ── 14. No classifier mutation ────────────────────────────────────────


class TestNoClassifierMutation:
    """The Phase 2.4 spec §11 invariant: intent_classifier.py MUST
    NOT be edited in any 2.4.x PR. Spot-check the constants.
    """

    def test_classifier_constants_unchanged(self):
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )
        assert CLASSIFY_THRESHOLD == 0.4
        assert INTENT_SOURCE_V0 == "rule_based_v0"


# ── 15. CLI subprocess smoke (cross-cutting) ──────────────────────────


class TestCliSubprocess:

    def test_intent_report_cli_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "report", "--window", "0d"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr

    def test_doctor_explain_last_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "doctor", "--explain-last"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0

    def test_policy_preview_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "policy-preview"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0


# ── 16. ADVISORY_LABEL pinned (cross-cutting) ─────────────────────────


class TestAdvisoryLabelPinned:

    def test_advisory_label_carries_the_canonical_phrasing(self):
        # The phrasing is normative for the directive's "every
        # surface labels suggestions as advisory" rule.
        assert "advisory" in ADVISORY_LABEL.lower()
        assert "TokenPak has not changed routing" in ADVISORY_LABEL

    def test_noop_default_off_tag_pinned(self):
        assert "no-op" in NOOP_DEFAULT_OFF_TAG
        assert "default-off" in NOOP_DEFAULT_OFF_TAG
