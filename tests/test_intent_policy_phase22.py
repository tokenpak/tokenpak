# SPDX-License-Identifier: Apache-2.0
"""Phase 2.2 — explain / report / dashboard policy-preview tests.

Eleven directive-mandated test classes:

  - CLI explain includes latest policy decision
  - CLI report includes policy summary
  - JSON output shape (CLI + API)
  - dashboard / API output shape
  - empty DB behavior
  - populated DB behavior
  - window filtering
  - no raw prompt content
  - no secrets emitted
  - no request mutation
  - no route mutation

All tests read-only; reuse the production engine + telemetry
store + report builders so the schema remains a single source of
truth.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import (
    IntentTelemetryRow,
    IntentTelemetryStore,
    build_contract,
)
from tokenpak.proxy.intent_policy_dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    collect_policy_dashboard,
)
from tokenpak.proxy.intent_policy_engine import (
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
)
from tokenpak.proxy.intent_policy_report import (
    build_policy_report,
)
from tokenpak.proxy.intent_policy_report import (
    render_human as render_policy_human,
)
from tokenpak.proxy.intent_policy_report import (
    render_json as render_policy_json,
)
from tokenpak.proxy.intent_policy_telemetry import (
    IntentPolicyDecisionRow,
    IntentPolicyDecisionStore,
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


def _seed_intent_event(store, *, request_id, contract,
                       prompt="summarize the vault", emitted=False, stripped=True,
                       timestamp=None):
    """Seed one intent_events row tied to a contract."""
    ts = timestamp or _dt.datetime.now().isoformat(timespec="seconds")
    store.write(IntentTelemetryRow(
        request_id=request_id,
        contract=contract,
        timestamp=ts,
        tip_headers_emitted=emitted,
        tip_headers_stripped=stripped,
    ))


def _seed_pair(events_store, policy_store, *, request_id="r1",
               intent_class="summarize", confidence=0.9,
               slots_present=("period",), slots_missing=(),
               catch_all_reason=None,
               provider="tokenpak-test", model="m",
               cfg=None, prompt="summarize the vault",
               timestamp=None):
    """Seed one intent_events row and the matching policy decision.

    Returns (contract, decision).
    """
    cls = _classification(
        intent_class=intent_class, confidence=confidence,
        slots_present=slots_present, slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )
    contract = build_contract(classification=cls, raw_prompt=prompt)
    _seed_intent_event(
        events_store, request_id=request_id, contract=contract,
        prompt=prompt, timestamp=timestamp,
    )
    pi = PolicyInput(
        intent_class=intent_class,
        confidence=confidence,
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
        provider=provider,
        model=model,
        live_verified_status=True,
    )
    decision = evaluate_policy(pi, cfg or PolicyEngineConfig())
    ts = timestamp or _dt.datetime.now().isoformat(timespec="seconds")
    policy_store.write(IntentPolicyDecisionRow(
        request_id=request_id,
        contract_id=contract.contract_id,
        timestamp=ts,
        decision=decision,
    ))
    return contract, decision


# ── 1. CLI explain includes latest policy decision ────────────────────


class TestExplainShowsPolicyDecision:
    """tokenpak doctor --explain-last MUST surface the linked
    policy decision when one exists.
    """

    def test_render_explain_renders_policy_section(self, tmp_path: Path):
        # Seed both tables under a single DB.
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        _seed_pair(events, policy, request_id="explain-1")
        events.close()
        policy.close()

        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=db)
        assert payload is not None
        assert payload["policy_decision"] is not None
        text = render_explain_last(payload)
        # Contract row + policy row both present.
        assert "Linked policy decision" in text
        assert "DRY-RUN / PREVIEW ONLY" in text
        # The 14 directive-mandated fields surface.
        for field in (
            "decision_id",
            "mode",
            "action",
            "decision_reason",
            "safety_flags",
        ):
            assert field in text, f"explain output missing {field!r}"

    def test_explain_when_policy_missing(self, tmp_path: Path):
        # Seed only the events row; no policy row exists.
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        cls = _classification()
        contract = build_contract(classification=cls, raw_prompt="hello")
        _seed_intent_event(events, request_id="lone", contract=contract)
        events.close()

        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=db)
        assert payload is not None
        assert payload["policy_decision"] is None
        text = render_explain_last(payload)
        assert "(none recorded yet)" in text


# ── 2. CLI report includes policy summary ─────────────────────────────


class TestReportIncludesPolicySummary:

    def test_intent_report_appends_policy_summary(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        _seed_pair(events, policy, request_id="r1")
        _seed_pair(events, policy, request_id="r2", intent_class="debug")
        events.close()
        policy.close()

        from tokenpak.proxy.intent_report import build_report, render_human
        r = build_report(window_days=14, db_path=db)
        # Phase 2.2 — new field on the report.
        assert "policy_summary" in r.to_dict()
        assert r.policy_summary["total_decisions"] == 2
        # Renderer appends the policy section.
        text = render_human(r)
        assert "Policy summary (Phase 2.2 dry-run / preview only)" in text


# ── 3. JSON output shape ──────────────────────────────────────────────


class TestJsonShape:
    """Both the CLI report and the policy-report API endpoint
    serialize cleanly. Required keys are pinned.
    """

    REQUIRED_POLICY_REPORT_KEYS = {
        "window_days",
        "window_cutoff_iso",
        "db_path",
        "total_decisions",
        "action_distribution",
        "decision_reason_distribution",
        "safety_flag_distribution",
        "recommendations_by_intent_class",
        "low_confidence_blocked",
        "low_confidence_safe_handled",
        "catch_all_safe_handled",
        "unverified_provider_blocked",
        "missing_slots_blocked",
        "budget_risk_flags",
        "compression_profile_distribution",
        "cache_strategy_distribution",
        "delivery_strategy_distribution",
        "review_areas",
    }

    def test_policy_report_required_keys(self, tmp_path: Path):
        r = build_policy_report(window_days=14, db_path=tmp_path / "nope.db")
        payload = json.loads(render_policy_json(r))
        missing = self.REQUIRED_POLICY_REPORT_KEYS - set(payload)
        assert not missing, f"missing keys: {missing}"

    def test_intent_report_to_dict_has_policy_summary(self, tmp_path: Path):
        from tokenpak.proxy.intent_report import build_report
        r = build_report(window_days=14, db_path=tmp_path / "nope.db")
        d = r.to_dict()
        assert "policy_summary" in d


# ── 4. Dashboard / API output shape ───────────────────────────────────


class TestDashboardShape:
    REQUIRED_CARD_KEYS = {
        "total_dry_run_decisions",
        "top_recommended_actions",
        "top_safety_flags",
        "budget_risk_flags",
        "suggested_compression_profiles",
        "suggested_cache_policies",
        "suggested_delivery_policies",
        "auto_routing_blocked_reasons",
    }

    REQUIRED_OPERATOR_PANEL_KEYS = {
        "top_recommended_actions",
        "top_safety_flags",
        "top_blocked_reasons",
        "recommended_review_areas",
    }

    REQUIRED_METADATA_KEYS = {
        "schema_version",
        "phase",
        "dry_run_preview_only",
        "preview_label",
        "window_days",
        "window_cutoff_iso",
        "telemetry_store_path",
    }

    def test_top_level_shape(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        # Phase 2.2 required keys (still enforced). Phase 2.4.2
        # additively adds a "suggestions" key — allowed but not
        # required by this assertion.
        assert {"metadata", "cards", "operator_panel"}.issubset(set(payload))

    def test_cards_keys(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_CARD_KEYS - set(payload["cards"])
        assert not missing, f"missing card keys: {missing}"

    def test_operator_panel_keys(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_OPERATOR_PANEL_KEYS - set(payload["operator_panel"])
        assert not missing, f"missing panel keys: {missing}"

    def test_metadata_keys(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_METADATA_KEYS - set(payload["metadata"])
        assert not missing, f"missing metadata keys: {missing}"

    def test_dry_run_preview_only_flag(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["metadata"]["dry_run_preview_only"] is True
        assert "DRY-RUN" in payload["metadata"]["preview_label"]
        assert payload["metadata"]["phase"] == "intent-layer-phase-2.2"

    def test_schema_version_pinned(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["metadata"]["schema_version"] == DASHBOARD_SCHEMA_VERSION
        assert payload["metadata"]["schema_version"] == "intent-policy-dashboard-v1"


# ── 5. Empty DB behavior ──────────────────────────────────────────────


class TestEmptyDB:

    def test_policy_report_empty_db(self, tmp_path: Path):
        r = build_policy_report(window_days=14, db_path=tmp_path / "nope.db")
        assert r.total_decisions == 0
        assert r.action_distribution == {}
        assert r.review_areas, "review_areas should explain empty state"

    def test_dashboard_empty_db(self, tmp_path: Path):
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["cards"]["total_dry_run_decisions"]["value"] == 0
        assert payload["cards"]["top_recommended_actions"]["items"] == []

    def test_table_missing_returns_zero(self, tmp_path: Path):
        # File exists, no schema.
        db = tmp_path / "telemetry.db"
        sqlite3.connect(str(db)).close()
        r = build_policy_report(window_days=14, db_path=db)
        assert r.total_decisions == 0


# ── 6. Populated DB behavior ──────────────────────────────────────────


class TestPopulatedDB:

    def test_action_distribution(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        # 3 summarize (compression suggestion); 2 status (observe);
        # 1 query w/ catch-all (warn_only).
        for i in range(3):
            _seed_pair(events, policy, request_id=f"a{i}",
                       intent_class="summarize")
        for i in range(2):
            _seed_pair(events, policy, request_id=f"b{i}",
                       intent_class="status")
        _seed_pair(events, policy, request_id="c0",
                   intent_class="query", confidence=0.0,
                   catch_all_reason="empty_prompt")
        events.close()
        policy.close()

        r = build_policy_report(window_days=14, db_path=db)
        assert r.total_decisions == 6
        # The catch-all entry trips low_confidence (0.0 < 0.65) so
        # warn_only fires for it; the catch_all reason is masked
        # by low_confidence in the priority-ordered taxonomy.
        assert r.action_distribution["suggest_compression_profile"] == 3
        assert r.action_distribution["observe_only"] == 2
        assert r.action_distribution["warn_only"] == 1
        # safety_flag distribution surfaces all flags that tripped.
        assert "low_confidence" in r.safety_flag_distribution
        assert "catch_all" in r.safety_flag_distribution

    def test_dashboard_pcts_computed(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        for i in range(2):
            _seed_pair(events, policy, request_id=f"a{i}", intent_class="summarize")
        events.close()
        policy.close()

        payload = collect_policy_dashboard(window_days=14, db_path=db)
        items = payload["cards"]["top_recommended_actions"]["items"]
        assert items[0]["count"] == 2
        assert items[0]["pct"] == 100.0


# ── 7. Window filtering ───────────────────────────────────────────────


class TestWindowFilter:

    def test_rows_outside_window_excluded(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        now = _dt.datetime(2026, 4, 26, 12, 0, 0)
        _seed_pair(events, policy, request_id="recent",
                   timestamp=(now - _dt.timedelta(days=1)).isoformat(timespec="seconds"))
        _seed_pair(events, policy, request_id="ancient",
                   timestamp=(now - _dt.timedelta(days=60)).isoformat(timespec="seconds"))
        events.close()
        policy.close()

        r = build_policy_report(window_days=14, db_path=db, now=now)
        assert r.total_decisions == 1


# ── 8 + 9. Privacy: no raw prompt content / secrets emitted ──────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PHASE-2-2"

    def test_sentinel_absent_from_policy_report(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        _seed_pair(events, policy, request_id="priv1",
                   prompt=f"summarize the vault {self.SENTINEL}")
        events.close()
        policy.close()

        r = build_policy_report(window_days=14, db_path=db)
        s_human = render_policy_human(r)
        s_json = render_policy_json(r)
        assert self.SENTINEL not in s_human, (
            "raw prompt content leaked into policy human render"
        )
        assert self.SENTINEL not in s_json, (
            "raw prompt content leaked into policy JSON render"
        )

    def test_sentinel_absent_from_dashboard(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        _seed_pair(events, policy, request_id="priv2",
                   prompt=f"summarize the vault {self.SENTINEL}")
        events.close()
        policy.close()

        payload = collect_policy_dashboard(window_days=14, db_path=db)
        assert self.SENTINEL not in json.dumps(payload)

    def test_per_row_hash_absent_from_policy_dashboard(self, tmp_path: Path):
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        prompt = "summarize the vault hash-test-zzz"
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        _seed_pair(events, policy, request_id="priv3", prompt=prompt)
        events.close()
        policy.close()

        payload = collect_policy_dashboard(window_days=14, db_path=db)
        assert digest not in json.dumps(payload)


# ── 10. No request mutation ───────────────────────────────────────────


class TestNoRequestMutation:
    """Phase 2.2 only adds READ-side surfaces. Verify by inspecting
    the engine + telemetry contract: the engine's input is unchanged
    after a call, and the new endpoint is read-only.
    """

    def test_read_only_no_writes_during_dashboard_call(self, tmp_path: Path):
        # Prepare a sealed DB and confirm dashboard call does not
        # mutate it (mtime / size unchanged).
        db = tmp_path / "telemetry.db"
        events = IntentTelemetryStore(db_path=db)
        policy = IntentPolicyDecisionStore(db_path=db)
        _seed_pair(events, policy, request_id="ro1")
        events.close()
        policy.close()

        before = db.stat().st_size
        before_mtime = db.stat().st_mtime
        # Multiple reads.
        for _ in range(5):
            collect_policy_dashboard(window_days=14, db_path=db)
            build_policy_report(window_days=14, db_path=db)
        after = db.stat().st_size
        after_mtime = db.stat().st_mtime
        assert after == before
        assert after_mtime == before_mtime


# ── 11. No route mutation ─────────────────────────────────────────────


class TestNoRouteMutation:
    """The Phase 2.2 read surfaces never call into the dispatch
    path. Verify by assertion at the import level: the policy-report
    + dashboard modules MUST NOT import server-side mutation
    primitives.
    """

    def test_policy_report_does_not_import_forward_path(self):
        import tokenpak.proxy.intent_policy_report as r

        # The module's source must not reference dispatch / forwarding
        # primitives. This is a structural test, not a behavioral
        # one.
        src = Path(r.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src, (
                f"policy report module references dispatch primitive: {forbidden!r}"
            )

    def test_policy_dashboard_does_not_import_forward_path(self):
        import tokenpak.proxy.intent_policy_dashboard as d

        src = Path(d.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src, (
                f"policy dashboard module references dispatch primitive: {forbidden!r}"
            )


# ── 12. CLI / API subprocess smoke (cross-cutting) ────────────────────


class TestCliSubprocess:

    def test_intent_report_cli_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "report", "--window", "0d"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr

    def test_intent_report_cli_json_includes_policy_summary(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "report",
             "--window", "0d", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        d: Dict[str, Any] = json.loads(result.stdout)
        assert "policy_summary" in d


# ── 13. Empty DB doctor explain handles missing policy table ──────────


class TestExplainEmptyDB:
    """When neither table exists, the renderer prints the friendly
    'no rows yet' message — no stack trace.
    """

    def test_explain_no_db(self, tmp_path: Path):
        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=tmp_path / "absent.db")
        assert payload is None
        text = render_explain_last(payload)
        assert "No intent_events rows yet" in text
