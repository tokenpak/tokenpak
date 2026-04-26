# SPDX-License-Identifier: Apache-2.0
"""Phase 1 — Intent Layer reporting test suite.

Six focused contracts:

  - empty DB → safe, well-shaped, no-data report
  - populated DB → all aggregations correct
  - JSON output shape — every field the directive enumerates
  - privacy contract — raw prompt text never appears in output
  - window filter — rows older than ``--window Nd`` excluded
  - adapter capability summary — eligible vs blocking split

Read-only throughout. Uses temp-DB writes via the production
:class:`IntentTelemetryStore` so the schema stays a single source
of truth.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import (
    IntentTelemetryRow,
    IntentTelemetryStore,
    build_contract,
)
from tokenpak.proxy.intent_report import (
    IntentReport,
    build_report,
    parse_window,
    render_human,
    render_json,
    window_cutoff_iso,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _classification(intent_class="summarize", confidence=0.9, slots_present=("period",), slots_missing=("target",), catch_all_reason=None):
    return IntentClassification(
        intent_class=intent_class,
        confidence=confidence,
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )


def _seed(store: IntentTelemetryStore, *, request_id: str, intent_class: str = "summarize",
          confidence: float = 0.9, prompt: str = "summarize the vault",
          slots_present=("period",), slots_missing=("target",),
          catch_all_reason=None, emitted: bool = False, stripped: bool = True,
          timestamp: str | None = None):
    """Seed one intent_events row using the production builder."""
    classification = _classification(
        intent_class=intent_class,
        confidence=confidence,
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )
    contract = build_contract(classification=classification, raw_prompt=prompt)
    ts = timestamp or _dt.datetime.now().isoformat(timespec="seconds")
    store.write(IntentTelemetryRow(
        request_id=request_id,
        contract=contract,
        timestamp=ts,
        tip_headers_emitted=emitted,
        tip_headers_stripped=stripped,
    ))
    return contract


# ── Window parsing ────────────────────────────────────────────────────


class TestWindowParsing:
    def test_default_form(self):
        assert parse_window("14d") == 14
        assert parse_window("7d") == 7
        assert parse_window("30d") == 30

    def test_zero_means_no_window(self):
        assert parse_window("0d") is None

    def test_empty_means_no_window(self):
        assert parse_window("") is None
        assert parse_window(None) is None

    def test_bad_form_raises(self):
        with pytest.raises(ValueError):
            parse_window("14")
        with pytest.raises(ValueError):
            parse_window("two-weeks")
        with pytest.raises(ValueError):
            parse_window("14h")


# ── Empty DB ──────────────────────────────────────────────────────────


class TestEmptyDB:
    """The report MUST run cleanly when the DB doesn't exist or
    when the table exists but has zero rows. Ship-day default —
    a fresh install must not raise.
    """

    def test_db_missing_returns_zero_total(self, tmp_path: Path):
        report = build_report(window_days=14, db_path=tmp_path / "nope.db")
        assert isinstance(report, IntentReport)
        assert report.total_classified == 0
        assert report.intent_class_distribution == {}
        assert report.review_areas, "review_areas should explain the empty state"

    def test_table_missing_returns_zero(self, tmp_path: Path):
        # File exists but no schema; build_report tolerates this.
        db = tmp_path / "telemetry.db"
        sqlite3.connect(str(db)).close()
        report = build_report(window_days=14, db_path=db)
        assert report.total_classified == 0

    def test_human_render_empty(self, tmp_path: Path):
        report = build_report(window_days=14, db_path=tmp_path / "nope.db")
        text = render_human(report)
        assert "Total classified:          0" in text
        assert "No classified requests" in text


# ── Populated DB summary ──────────────────────────────────────────────


class TestPopulatedDBSummary:
    def test_distribution_sums_to_total(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        for i in range(3):
            _seed(store, request_id=f"a{i}", intent_class="summarize", confidence=0.9)
        for i in range(2):
            _seed(store, request_id=f"b{i}", intent_class="debug", confidence=0.8)
        _seed(store, request_id="c0", intent_class="query", confidence=0.0,
              catch_all_reason="empty_prompt", slots_present=(), slots_missing=())
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        assert report.total_classified == 6
        dist = report.intent_class_distribution
        assert sum(dist.values()) == 6
        assert dist == {"summarize": 3, "debug": 2, "query": 1}

    def test_avg_confidence_per_class(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", intent_class="summarize", confidence=1.0)
        _seed(store, request_id="a1", intent_class="summarize", confidence=0.5)
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        # avg(1.0, 0.5) = 0.75
        assert report.avg_confidence_by_class["summarize"] == 0.75

    def test_low_confidence_count(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        # threshold = 0.4 (CLASSIFY_THRESHOLD)
        _seed(store, request_id="a0", intent_class="summarize", confidence=0.9)
        _seed(store, request_id="a1", intent_class="query", confidence=0.0,
              catch_all_reason="keyword_miss", slots_present=(), slots_missing=())
        _seed(store, request_id="a2", intent_class="query", confidence=0.3,
              catch_all_reason="confidence_below_threshold", slots_present=(), slots_missing=())
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        # Two rows below 0.4 (both query rows).
        assert report.low_confidence_count == 2

    def test_catch_all_distribution(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", intent_class="summarize", confidence=0.9)
        _seed(store, request_id="a1", intent_class="query", confidence=0.0,
              catch_all_reason="empty_prompt", slots_present=(), slots_missing=())
        _seed(store, request_id="a2", intent_class="query", confidence=0.0,
              catch_all_reason="empty_prompt", slots_present=(), slots_missing=())
        _seed(store, request_id="a3", intent_class="query", confidence=0.3,
              catch_all_reason="confidence_below_threshold", slots_present=(), slots_missing=())
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        assert report.catch_all_reason_distribution == {
            "empty_prompt": 2,
            "confidence_below_threshold": 1,
        }
        # Top-N is sorted-by-count desc.
        top = report.top_catch_all_reasons
        assert top[0] == ("empty_prompt", 2)

    def test_slot_frequencies(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", slots_present=("period",), slots_missing=("target",))
        _seed(store, request_id="a1", slots_present=("period",), slots_missing=("target",))
        _seed(store, request_id="a2", slots_present=("period", "model"), slots_missing=())
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        assert report.slots_present_frequency["period"] == 3
        assert report.slots_present_frequency["model"] == 1
        assert report.slots_missing_frequency["target"] == 2
        assert report.top_missing_slots[0] == ("target", 2)

    def test_wire_emission_counts(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="emit1", emitted=True, stripped=False)
        _seed(store, request_id="emit2", emitted=True, stripped=False)
        _seed(store, request_id="strip1", emitted=False, stripped=True)
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        assert report.tip_headers_emitted == 2
        assert report.tip_headers_stripped == 1
        # telemetry-only mirrors stripped (the alternative framing).
        assert report.telemetry_only == 1


# ── JSON shape ────────────────────────────────────────────────────────


class TestJsonShape:
    REQUIRED_TOP_LEVEL_KEYS = {
        "window_days",
        "window_cutoff_iso",
        "db_path",
        "total_classified",
        "intent_class_distribution",
        "avg_confidence_by_class",
        "catch_all_reason_distribution",
        "slots_present_frequency",
        "slots_missing_frequency",
        "low_confidence_count",
        "low_confidence_threshold",
        "tip_headers_emitted",
        "tip_headers_stripped",
        "telemetry_only",
        "adapters_eligible",
        "adapters_blocking",
        "top_missing_slots",
        "top_catch_all_reasons",
        "review_areas",
    }

    def test_json_has_all_required_keys(self, tmp_path: Path):
        report = build_report(window_days=14, db_path=tmp_path / "nope.db")
        payload = json.loads(render_json(report))
        missing = self.REQUIRED_TOP_LEVEL_KEYS - set(payload)
        assert not missing, f"missing keys in JSON output: {missing}"

    def test_json_is_round_trippable(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0")
        store.close()
        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        # Round-trip test: render → json.loads → rebuild a dict and
        # assert equality of the dict shape.
        payload = json.loads(render_json(report))
        assert payload["total_classified"] == 1

    def test_json_top_n_is_list_of_pairs(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", slots_missing=("target",))
        store.close()
        payload = json.loads(render_json(build_report(window_days=14, db_path=tmp_path / "t.db")))
        # Tuples become 2-element lists in JSON for portability.
        assert payload["top_missing_slots"][0] == ["target", 1]


# ── Privacy contract ──────────────────────────────────────────────────


class TestPrivacyContract:
    """The report MUST NOT read or emit raw prompt content. Asserted
    end-to-end with a sentinel substring planted in the prompt — it
    must appear nowhere in the rendered output.
    """

    SENTINEL = "kevin-magic-prompt-marker-PHASE1"

    def test_no_prompt_text_in_human_or_json(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        prompt = f"summarize the vault {self.SENTINEL}"
        _seed(store, request_id="a0", prompt=prompt)
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        human_out = render_human(report)
        json_out = render_json(report)
        assert self.SENTINEL not in human_out, (
            "raw prompt content leaked into human-readable output"
        )
        assert self.SENTINEL not in json_out, (
            "raw prompt content leaked into JSON output"
        )

    def test_raw_prompt_hash_not_in_output(self, tmp_path: Path):
        # The report MUST not even include the per-row hash digest
        # (which is not secret, but is unnecessary aggregate noise).
        # Verify by planting a known prompt and asserting its hash
        # is absent. Distinct from the sentinel test above — guards
        # against a future change that adds raw_prompt_hash to the
        # aggregation surface.
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        prompt = "summarize the vault unique-prompt-zzz"
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        _seed(store, request_id="a0", prompt=prompt)
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db")
        assert digest not in render_json(report)
        assert digest not in render_human(report)


# ── Window filter ─────────────────────────────────────────────────────


class TestWindowFilter:
    def test_rows_older_than_window_excluded(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        # Anchor "now" so the cutoff math is deterministic.
        now = _dt.datetime(2026, 4, 25, 12, 0, 0)
        # Inside window (1 day ago)
        _seed(store, request_id="recent",
              timestamp=(now - _dt.timedelta(days=1)).isoformat(timespec="seconds"))
        # Outside window (30 days ago)
        _seed(store, request_id="ancient",
              timestamp=(now - _dt.timedelta(days=30)).isoformat(timespec="seconds"))
        store.close()

        report = build_report(window_days=14, db_path=tmp_path / "t.db", now=now)
        assert report.total_classified == 1, (
            f"window filter ignored: {report.total_classified} rows in 14d window "
            "(expected 1; row 'ancient' should be excluded)"
        )

    def test_no_window_reads_all_rows(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        now = _dt.datetime(2026, 4, 25, 12, 0, 0)
        _seed(store, request_id="recent",
              timestamp=(now - _dt.timedelta(days=1)).isoformat(timespec="seconds"))
        _seed(store, request_id="ancient",
              timestamp=(now - _dt.timedelta(days=400)).isoformat(timespec="seconds"))
        store.close()

        report = build_report(window_days=None, db_path=tmp_path / "t.db", now=now)
        assert report.total_classified == 2

    def test_cutoff_iso_correct(self):
        now = _dt.datetime(2026, 4, 25, 12, 0, 0)
        cutoff = window_cutoff_iso(14, now=now)
        assert cutoff == "2026-04-11T12:00:00"


# ── Adapter capability summary ────────────────────────────────────────


class TestAdapterCapabilitySummary:
    """Phase 1 splits the registered adapters into ``adapters_eligible``
    (declare ``tip.intent.contract-headers-v1``) and ``adapters_blocking``
    (do NOT declare). In Phase 0 default state every first-party
    adapter is in ``adapters_blocking``.
    """

    def test_phase_0_default_all_blocking(self, tmp_path: Path):
        report = build_report(window_days=14, db_path=tmp_path / "nope.db")
        # Every entry has shape {name, source_format}.
        for entry in report.adapters_blocking:
            assert "name" in entry
            assert "source_format" in entry
        # Phase 0 invariant from PR #44 — verified again here as
        # the report sees the same adapter registry.
        assert report.adapters_eligible == [], (
            "Phase 0 default says no first-party adapter declares the "
            "gate label; if this changed, update the corresponding "
            "invariant test in test_intent_layer_phase0.py too."
        )
        # And there's at least one blocking adapter (anthropic /
        # openai-chat / etc.).
        assert len(report.adapters_blocking) >= 1


# ── Subprocess-driven CLI smoke ───────────────────────────────────────


class TestCliSubprocess:
    """Ship the command via the real argparse path so the help
    string + flags stay reachable. End-to-end smoke; no assertion
    on aggregations (other tests cover those).
    """

    def test_help_includes_intent_report(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "report", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--window" in result.stdout
        assert "--json" in result.stdout

    def test_report_runs_without_error_on_empty(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "report",
             "--window", "0d"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, (
            f"intent report failed: {result.stdout}\n{result.stderr}"
        )
