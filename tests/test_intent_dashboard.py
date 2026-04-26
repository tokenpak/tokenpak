# SPDX-License-Identifier: Apache-2.0
"""Phase 1.1 — Intent Layer dashboard / read-model regression suite.

Seven contracts, one per directive bullet:

  - read-model output from empty DB
  - read-model output from populated DB
  - window filtering
  - JSON / API schema stability
  - no raw prompt content emitted
  - adapter capability summary
  - dashboard / API handles missing telemetry.db gracefully

Read-only throughout. Reuses :class:`IntentTelemetryStore` (Phase
0) to seed temp DBs so the schema stays a single source of truth.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import (
    IntentTelemetryRow,
    IntentTelemetryStore,
    build_contract,
)
from tokenpak.proxy.intent_dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    collect_dashboard,
    parse_window_or_default,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _classification(intent_class="summarize", confidence=0.9,
                    slots_present=("period",), slots_missing=("target",),
                    catch_all_reason=None):
    return IntentClassification(
        intent_class=intent_class,
        confidence=confidence,
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=catch_all_reason,
    )


def _seed(store, *, request_id, intent_class="summarize", confidence=0.9,
          prompt="summarize the vault", slots_present=("period",),
          slots_missing=("target",), catch_all_reason=None,
          emitted=False, stripped=True, timestamp=None):
    classification = _classification(
        intent_class=intent_class, confidence=confidence,
        slots_present=slots_present, slots_missing=slots_missing,
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


# ── 1. Empty DB ───────────────────────────────────────────────────────


class TestReadModelEmptyDB:
    """Acceptance bullet 1 — read-model handles an empty store
    cleanly. Every card slot is present, with zero/empty values.
    """

    def test_db_missing_returns_well_shaped(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert "metadata" in payload
        assert "cards" in payload
        assert "operator_panel" in payload
        assert payload["cards"]["total_classified"]["value"] == 0
        assert payload["cards"]["intent_class_distribution"]["items"] == []
        assert payload["cards"]["catch_all_reason_distribution"]["items"] == []

    def test_db_missing_recommended_review_areas_populated(self, tmp_path: Path):
        # Operator panel still surfaces a meaningful review area when
        # the DB is empty (the "no rows yet" hint).
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        areas = payload["operator_panel"]["recommended_review_areas"]
        assert len(areas) >= 1

    def test_db_missing_avg_confidence_is_zero_not_null(self, tmp_path: Path):
        # Dashboards prefer 0.0 to None for numeric fields — easier
        # to render. The contract pins this.
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["cards"]["average_confidence"]["value"] == 0.0


# ── 2. Populated DB ───────────────────────────────────────────────────


class TestReadModelPopulatedDB:
    """Acceptance bullet 2 — every card has the expected counts /
    derived values when the store has data. Pre-computed
    percentages match what the Phase 1 report would produce.
    """

    def test_total_and_distribution(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        for i in range(3):
            _seed(store, request_id=f"a{i}", intent_class="summarize", confidence=0.9)
        for i in range(2):
            _seed(store, request_id=f"b{i}", intent_class="debug", confidence=0.6)
        store.close()

        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db")
        assert payload["cards"]["total_classified"]["value"] == 5

        items = {
            i["intent_class"]: i
            for i in payload["cards"]["intent_class_distribution"]["items"]
        }
        assert items["summarize"]["count"] == 3
        assert items["summarize"]["pct"] == 60.0
        assert items["debug"]["count"] == 2
        assert items["debug"]["pct"] == 40.0

    def test_low_confidence_card(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="hi", confidence=0.9)
        _seed(store, request_id="lo", intent_class="query", confidence=0.0,
              catch_all_reason="empty_prompt", slots_present=(), slots_missing=())
        store.close()

        card = collect_dashboard(window_days=14, db_path=tmp_path / "t.db") \
            ["cards"]["low_confidence_count"]
        assert card["value"] == 1
        assert card["pct_of_total"] == 50.0
        assert card["threshold"] == 0.4

    def test_wire_emission_card_with_pcts(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="emit", emitted=True, stripped=False)
        _seed(store, request_id="strip1", emitted=False, stripped=True)
        _seed(store, request_id="strip2", emitted=False, stripped=True)
        store.close()

        card = collect_dashboard(window_days=14, db_path=tmp_path / "t.db") \
            ["cards"]["tip_headers_emitted_vs_telemetry_only"]
        assert card["tip_headers_emitted"] == 1
        assert card["telemetry_only"] == 2
        assert card["tip_headers_stripped"] == 2
        # Pre-computed percentages.
        assert card["emitted_pct"] == round(100.0 * 1 / 3, 1)
        assert card["telemetry_only_pct"] == round(100.0 * 2 / 3, 1)

    def test_top_missing_slots_card(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", slots_missing=("target",))
        _seed(store, request_id="a1", slots_missing=("target",))
        _seed(store, request_id="a2", slots_missing=("period",))
        store.close()

        card = collect_dashboard(window_days=14, db_path=tmp_path / "t.db") \
            ["cards"]["top_missing_slots"]
        items = card["items"]
        assert items[0] == {"slot": "target", "count": 2, "pct": round(100.0 * 2 / 3, 1)}
        assert items[1] == {"slot": "period", "count": 1, "pct": round(100.0 * 1 / 3, 1)}

    def test_average_confidence_volume_weighted(self, tmp_path: Path):
        # Volume-weighted: 2 rows at 1.0 (summarize) + 2 rows at 0.5 (debug)
        # = (2*1.0 + 2*0.5) / 4 = 0.75 across all classifications.
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", intent_class="summarize", confidence=1.0)
        _seed(store, request_id="a1", intent_class="summarize", confidence=1.0)
        _seed(store, request_id="b0", intent_class="debug", confidence=0.5)
        _seed(store, request_id="b1", intent_class="debug", confidence=0.5)
        store.close()

        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db")
        assert payload["cards"]["average_confidence"]["value"] == 0.75


# ── 3. Window filtering ───────────────────────────────────────────────


class TestWindowFilter:
    """Acceptance bullet 3 — rows older than ``window_days``
    excluded. The dashboard surface defaults to 14d when no
    explicit window is provided (distinct from the CLI's '0d ='
    all-rows behavior — the API default-14d posture prevents a
    naïve curl from accidentally surfacing multi-year history).
    """

    def test_rows_outside_window_excluded(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        now = _dt.datetime(2026, 4, 25, 12, 0, 0)
        _seed(store, request_id="recent",
              timestamp=(now - _dt.timedelta(days=2)).isoformat(timespec="seconds"))
        _seed(store, request_id="ancient",
              timestamp=(now - _dt.timedelta(days=60)).isoformat(timespec="seconds"))
        store.close()

        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db", now=now)
        assert payload["cards"]["total_classified"]["value"] == 1

    def test_default_window_is_14(self):
        # The dashboard wrapper for parse_window defaults to 14d
        # when the spec is missing or empty (vs the CLI's parse_window
        # which returns None for missing → "all rows").
        assert parse_window_or_default(None) == 14
        assert parse_window_or_default("") == 14

    def test_explicit_window_passes_through(self):
        assert parse_window_or_default("7d") == 7
        assert parse_window_or_default("30d") == 30

    def test_zero_window_means_all_rows(self):
        assert parse_window_or_default("0d") is None

    def test_bad_window_raises_for_api_400(self):
        with pytest.raises(ValueError):
            parse_window_or_default("forever")
        with pytest.raises(ValueError):
            parse_window_or_default("14h")


# ── 4. JSON / API schema stability ────────────────────────────────────


class TestSchemaStability:
    """Acceptance bullet 4 — the wire shape is the documented API
    contract. Pin the required-key sets so a future drift trips
    here loudly instead of silently breaking dashboard consumers.
    """

    REQUIRED_CARD_KEYS = {
        "total_classified",
        "intent_class_distribution",
        "average_confidence",
        "low_confidence_count",
        "catch_all_reason_distribution",
        "top_missing_slots",
        "tip_headers_emitted_vs_telemetry_only",
        "adapters_eligible",
        "adapters_blocking",
    }

    REQUIRED_OPERATOR_PANEL_KEYS = {
        "most_common_missing_slots",
        "most_common_catch_all_reasons",
        "adapters_eligible_for_tip_headers",
        "adapters_requiring_capability_declaration",
        "recommended_review_areas",
    }

    REQUIRED_METADATA_KEYS = {
        "schema_version",
        "window_days",
        "window_cutoff_iso",
        "telemetry_store_path",
        "low_confidence_threshold",
        "phase",
        "observation_only",
    }

    def test_top_level_shape(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert set(payload) == {"metadata", "cards", "operator_panel"}

    def test_cards_keys(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_CARD_KEYS - set(payload["cards"])
        assert not missing, f"missing card keys: {missing}"

    def test_operator_panel_keys(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_OPERATOR_PANEL_KEYS - set(payload["operator_panel"])
        assert not missing, f"missing operator_panel keys: {missing}"

    def test_metadata_keys(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        missing = self.REQUIRED_METADATA_KEYS - set(payload["metadata"])
        assert not missing, f"missing metadata keys: {missing}"

    def test_schema_version_pinned(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["metadata"]["schema_version"] == DASHBOARD_SCHEMA_VERSION
        assert payload["metadata"]["schema_version"] == "intent-dashboard-v1"

    def test_observation_only_flag_pinned(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["metadata"]["observation_only"] is True
        assert payload["metadata"]["phase"] == "intent-layer-phase-1.1"

    def test_payload_is_json_serialisable(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        _seed(store, request_id="a0", slots_missing=("target",))
        store.close()
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db")
        # Round-trip — all fields must serialise.
        json.dumps(payload)


# ── 5. Privacy contract ───────────────────────────────────────────────


class TestPrivacyContract:
    """Acceptance bullet 5 — no raw prompt content. Plant a
    sentinel substring in the prompt and assert it appears nowhere
    in the dashboard payload (neither human nor JSON form).
    """

    SENTINEL = "kevin-magic-prompt-marker-PHASE-1-1"

    def test_sentinel_absent_from_payload(self, tmp_path: Path):
        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        prompt = f"summarize the vault {self.SENTINEL}"
        _seed(store, request_id="a0", prompt=prompt)
        store.close()

        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db")
        serialized = json.dumps(payload)
        assert self.SENTINEL not in serialized, (
            "raw prompt content leaked into dashboard JSON"
        )

    def test_raw_prompt_hash_absent_from_payload(self, tmp_path: Path):
        # The hash isn't secret, but the dashboard surface MUST NOT
        # include it (it's a per-row dedup key, not an aggregation
        # input). Guards against a future change that pulls
        # raw_prompt_hash into the payload.
        import hashlib

        store = IntentTelemetryStore(db_path=tmp_path / "t.db")
        prompt = "summarize the vault unique-prompt-zzz-dashboard"
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        _seed(store, request_id="a0", prompt=prompt)
        store.close()

        payload = collect_dashboard(window_days=14, db_path=tmp_path / "t.db")
        assert digest not in json.dumps(payload)


# ── 6. Adapter capability summary ─────────────────────────────────────


class TestAdapterCapabilitySummary:
    """Acceptance bullet 6 — eligible vs blocking split. Phase 0
    default has every first-party adapter in 'blocking'; the
    dashboard surfaces both sides so an operator can see which
    adapters need to declare the gate label.
    """

    def test_split_shapes(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        eligible = payload["cards"]["adapters_eligible"]
        blocking = payload["cards"]["adapters_blocking"]
        assert "items" in eligible and "count" in eligible
        assert "items" in blocking and "count" in blocking
        assert eligible["count"] == len(eligible["items"])
        assert blocking["count"] == len(blocking["items"])

    def test_phase_0_default_all_blocking(self, tmp_path: Path):
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        # Phase 0 invariant: no first-party adapter declares the gate
        # label by default. This is also pinned in
        # tests/test_intent_layer_phase0.py — keep both in sync.
        assert payload["cards"]["adapters_eligible"]["count"] == 0
        assert payload["cards"]["adapters_blocking"]["count"] >= 1

    def test_operator_panel_mirrors_card_data(self, tmp_path: Path):
        # The operator panel rephrases the card data narratively; the
        # underlying lists must be the same (one card-side, one
        # panel-side, both reading the same source).
        payload = collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        cards = payload["cards"]
        panel = payload["operator_panel"]
        assert cards["adapters_eligible"]["items"] == \
            panel["adapters_eligible_for_tip_headers"]
        assert cards["adapters_blocking"]["items"] == \
            panel["adapters_requiring_capability_declaration"]


# ── 7. Missing telemetry.db handled gracefully ────────────────────────


class TestMissingTelemetryDb:
    """Acceptance bullet 7 — the API path MUST NOT raise when the
    telemetry.db doesn't exist (fresh install). The read-model
    returns a well-shaped zero payload; the API endpoint returns
    200 with that payload, never 5xx.
    """

    def test_missing_db_returns_zeroed_payload(self, tmp_path: Path):
        # Path that absolutely doesn't exist.
        absent = tmp_path / "absolutely-not-a-real-path" / "telemetry.db"
        payload = collect_dashboard(window_days=14, db_path=absent)
        assert payload["cards"]["total_classified"]["value"] == 0
        # Adapter posture still computed from the in-process
        # registry — independent of the DB.
        assert payload["cards"]["adapters_blocking"]["count"] >= 1

    def test_missing_db_does_not_raise(self, tmp_path: Path):
        # Best-effort contract. If this test ever flips, it means
        # the API endpoint will start emitting 5xx — bad for
        # dashboard polling.
        try:
            collect_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"collect_dashboard raised on missing DB: {exc!r}")
