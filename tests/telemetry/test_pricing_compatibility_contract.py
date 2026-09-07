"""Focused compatibility tests for the versioned pricing store."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tokenpak.telemetry import cost as cost_module
from tokenpak.telemetry.cost import (
    CURRENT_EFFECTIVE_DATE,
    CURRENT_PRICING_VERSION,
    PROVENANCE_CUSTOM,
    PROVENANCE_FALLBACK,
    PROVENANCE_SEED_REFRESH,
    UNIT_BASIS_USD_PER_1K,
    UNKNOWN_METADATA,
    CostEngine,
    Pricing,
    PricingRefreshCollisionError,
    PricingRefreshUnavailableError,
    StalePricingRefreshPlanError,
    UnknownPricingUnitError,
    calculate_baseline,
)
from tokenpak.telemetry.server import create_app
from tokenpak.telemetry.storage import TelemetryDB

LEGACY_DDL = """
CREATE TABLE tp_pricing (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version        TEXT    NOT NULL,
    effective_date DATE    NOT NULL,
    provider       TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    input_rate     REAL    NOT NULL,
    output_rate    REAL    NOT NULL,
    currency       TEXT    NOT NULL DEFAULT 'USD',
    source         TEXT    NOT NULL DEFAULT 'official',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def _legacy_db(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(LEGACY_DDL)
        conn.executemany(
            """INSERT INTO tp_pricing
               (version, effective_date, provider, model, input_rate, output_rate, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def _row(
    *,
    version: str = "legacy-v1",
    effective_date: str = "2026-01-01",
    provider: str = "custom-provider",
    model: str = "custom-model",
    input_rate: float = 1.25,
    output_rate: float = 6.5,
    source: str = "operator-file",
) -> tuple[object, ...]:
    return (version, effective_date, provider, model, input_rate, output_rate, source)


def _plan_one(engine: CostEngine, key: str = "openai/gpt-4o-mini") -> dict[str, object]:
    return engine.plan_seed_refresh(selected_models=[key], overrides={key: True})


def test_pricing_positional_api_remains_usd_per_1k() -> None:
    pricing = Pricing("custom", "model", 1.5, 7.5, "v1", "2026-01-01")
    assert pricing.input_per_token == pytest.approx(0.0015)
    assert pricing.output_per_token == pytest.approx(0.0075)
    assert calculate_baseline(1_000, 0, pricing) == pytest.approx(1.5)
    assert pricing.provenance == UNKNOWN_METADATA
    assert pricing.unit_basis == UNKNOWN_METADATA


def test_fresh_database_seeds_through_receipted_per_million_normalization(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    engine = CostEngine(str(db))

    pricing = engine.get_pricing("gpt-4o-mini")
    result = engine.calculate("gpt-4o-mini", 1_000_000, 1_000_000, 0)
    assert pricing.input_rate == pytest.approx(0.00015)
    assert pricing.output_rate == pytest.approx(0.00060)
    assert result.actual_cost == pytest.approx(0.15)
    assert pricing.provenance == PROVENANCE_SEED_REFRESH
    assert pricing.unit_basis == UNIT_BASIS_USD_PER_1K

    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT input_rate, provenance, unit_basis FROM tp_pricing WHERE model='gpt-4o-mini'"
        ).fetchone()
        receipt_count = conn.execute("SELECT COUNT(*) FROM tp_pricing_refresh_receipts").fetchone()[
            0
        ]
        unique_indexes = [
            row[1] for row in conn.execute("PRAGMA index_list(tp_pricing)") if row[2] and not row[4]
        ]
        unique_columns = [
            tuple(column[2] for column in conn.execute(f"PRAGMA index_info('{name}')"))
            for name in unique_indexes
        ]
    assert stored is not None
    assert stored[0] == pytest.approx(0.00015)
    assert stored[1:] == (PROVENANCE_SEED_REFRESH, UNIT_BASIS_USD_PER_1K)
    assert receipt_count == 1
    assert ("version", "provider", "model") in unique_columns


def test_fresh_fallback_is_known_and_strict_safe(tmp_path: Path) -> None:
    engine = CostEngine(str(tmp_path / "fallback.db"), strict_unknown_units=True)
    pricing = engine.get_pricing("model-that-does-not-exist")
    assert pricing.input_rate == pytest.approx(0.003)
    assert pricing.output_rate == pytest.approx(0.015)
    assert pricing.provenance == PROVENANCE_FALLBACK
    assert pricing.unit_basis == UNIT_BASIS_USD_PER_1K


def test_populated_initialization_is_schema_only_and_preserves_history(tmp_path: Path) -> None:
    db = tmp_path / "populated.db"
    original = _row()
    _legacy_db(db, [original])

    CostEngine(str(db))

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """SELECT version, effective_date, provider, model, input_rate,
                      output_rate, source, provenance, unit_basis
               FROM tp_pricing ORDER BY id"""
        ).fetchall()
        receipt_count = conn.execute("SELECT COUNT(*) FROM tp_pricing_refresh_receipts").fetchone()[
            0
        ]
    assert rows == [original + (None, None)]
    assert receipt_count == 0


def test_auto_seed_skips_when_another_writer_populates_the_init_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "init-race.db"
    original_plan = CostEngine.plan_seed_refresh
    injected = False

    def populate_before_plan(
        self: CostEngine, *args: object, **kwargs: object
    ) -> dict[str, object]:
        nonlocal injected
        if kwargs.get("require_empty") and not injected:
            injected = True
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO tp_pricing
                       (version, effective_date, provider, model, input_rate, output_rate,
                        source, provenance, unit_basis)
                       VALUES ('custom-v1', '2026-09-06', 'custom', 'race-winner',
                               1.0, 2.0, 'operator', 'CUSTOM', 'USD_PER_1K')"""
                )
        return original_plan(self, *args, **kwargs)

    monkeypatch.setattr(CostEngine, "plan_seed_refresh", populate_before_plan)
    engine = CostEngine(str(db))
    assert engine.get_pricing("race-winner").input_rate == pytest.approx(1.0)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT model FROM tp_pricing ORDER BY id").fetchall()
        receipts = conn.execute("SELECT COUNT(*) FROM tp_pricing_refresh_receipts").fetchone()[0]
    assert rows == [("race-winner",)]
    assert receipts == 0


def test_concurrent_empty_initializers_seed_once(tmp_path: Path) -> None:
    db = tmp_path / "concurrent-empty.db"
    with ThreadPoolExecutor(max_workers=2) as pool:
        engines = list(pool.map(lambda _unused: CostEngine(str(db)), range(2)))
    assert all(
        engine.get_pricing("gpt-4o-mini").input_rate == pytest.approx(0.00015) for engine in engines
    )
    with sqlite3.connect(db) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0]
        distinct_count = conn.execute(
            "SELECT COUNT(*) FROM (SELECT version, provider, model FROM tp_pricing GROUP BY 1,2,3)"
        ).fetchone()[0]
        receipt_count = conn.execute("SELECT COUNT(*) FROM tp_pricing_refresh_receipts").fetchone()[
            0
        ]
    assert row_count == distinct_count == len(cost_module.SEED_PRICING)
    assert receipt_count == 1


def test_legacy_units_stay_numeric_and_strict_mode_refuses_unknown(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _legacy_db(
        db,
        [
            _row(
                provider="openai",
                model="gpt-4o-mini",
                input_rate=0.15,
                output_rate=0.60,
            )
        ],
    )
    legacy = CostEngine(str(db))
    pricing = legacy.get_pricing("gpt-4o-mini")
    assert pricing.input_rate == pytest.approx(0.15)
    assert pricing.unit_basis == UNKNOWN_METADATA
    assert legacy.calculate("gpt-4o-mini", 1_000_000, 1_000_000, 0).actual_cost == pytest.approx(
        150.0
    )

    strict = CostEngine(str(db), strict_unknown_units=True)
    with pytest.raises(UnknownPricingUnitError, match="unit basis is unknown"):
        strict.get_pricing("gpt-4o-mini")


def test_add_pricing_is_custom_usd_per_1k_regardless_of_labels(tmp_path: Path) -> None:
    engine = CostEngine(str(tmp_path / "custom.db"))
    engine.add_pricing(
        "odd-provider",
        "custom-priced-model",
        1.25,
        8.75,
        version="official-looking",
        source="seed-looking",
    )
    pricing = engine.get_pricing("custom-priced-model")
    assert pricing.input_per_token == pytest.approx(0.00125)
    assert pricing.provenance == PROVENANCE_CUSTOM
    assert pricing.unit_basis == UNIT_BASIS_USD_PER_1K


def test_duplicate_rows_open_without_deletion_and_refresh_refuses(tmp_path: Path) -> None:
    db = tmp_path / "duplicates.db"
    _legacy_db(db, [_row(input_rate=1.0), _row(input_rate=2.0)])
    engine = CostEngine(str(db))

    assert engine.get_pricing("custom-model").input_rate == pytest.approx(2.0)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0] == 2
    with pytest.raises(PricingRefreshUnavailableError, match="duplicate key preserved"):
        _plan_one(engine)


def test_partial_unique_index_does_not_authorize_refresh(tmp_path: Path) -> None:
    db = tmp_path / "partial-index.db"
    _legacy_db(db, [_row()])
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE UNIQUE INDEX idx_tp_pricing_unique
               ON tp_pricing(version, provider, model)
               WHERE provider <> ''"""
        )

    engine = CostEngine(str(db))
    assert engine.get_pricing("custom-model").input_rate == pytest.approx(1.25)
    with pytest.raises(PricingRefreshUnavailableError, match="correctly shaped unique index"):
        _plan_one(engine)
    with sqlite3.connect(db) as conn:
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_tp_pricing_unique'"
        ).fetchone()[0]
        assert "WHERE provider <> ''" in index
        assert conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0] == 1


def test_expression_unique_index_does_not_authorize_refresh(tmp_path: Path) -> None:
    db = tmp_path / "expression-index.db"
    _legacy_db(db, [_row()])
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE UNIQUE INDEX idx_tp_pricing_unique
               ON tp_pricing(version, provider, model, lower(source))"""
        )
    engine = CostEngine(str(db))
    assert engine.get_pricing("custom-model").input_rate == pytest.approx(1.25)
    with pytest.raises(PricingRefreshUnavailableError, match="correctly shaped unique index"):
        _plan_one(engine)


def test_plan_requires_every_override_and_is_json_serializable(tmp_path: Path) -> None:
    db = tmp_path / "plan.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    with pytest.raises(cost_module.PricingRefreshError, match="explicit apply/skip"):
        engine.plan_seed_refresh(selected_models=["openai/gpt-4o-mini"], overrides={})

    plan = _plan_one(engine)
    assert json.loads(json.dumps(plan, sort_keys=True))["plan_hash"] == plan["plan_hash"]
    assert plan["pricing_row_count"] == 1
    assert plan["schema_columns"]
    assert plan["indexes"]
    selected = plan["selected_rows"][0]
    assert selected["seed_unit_basis"] == "USD_PER_1M"
    assert selected["seed"]["input_rate"] == pytest.approx(0.15)
    assert selected["target"]["input_rate"] == pytest.approx(0.00015)
    assert selected["target"]["unit_basis"] == UNIT_BASIS_USD_PER_1K


def test_plan_exposes_selected_seed_fuzzy_shadowing(tmp_path: Path) -> None:
    db = tmp_path / "selected-shadowing.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    keys = ["openai/gpt-5", "openai/gpt-5.6"]
    plan = engine.plan_seed_refresh(
        selected_models=keys,
        overrides={key: True for key in keys},
    )
    gpt5_shadowing = plan["shadowing"]["openai/gpt-5"]
    assert any(
        row["model"] == "gpt-5.6" and "selected_fuzzy_model" in row["relations"]
        for row in gpt5_shadowing
    )


def test_exact_key_collision_requires_skip_or_distinct_version(tmp_path: Path) -> None:
    db = tmp_path / "collision.db"
    _legacy_db(
        db,
        [
            _row(
                version=CURRENT_PRICING_VERSION,
                effective_date="2025-01-01",
                provider="openai",
                model="gpt-4o-mini",
                source="dev",
            )
        ],
    )
    engine = CostEngine(str(db))
    key = "openai/gpt-4o-mini"
    with pytest.raises(PricingRefreshCollisionError, match="distinct version"):
        engine.plan_seed_refresh(
            selected_models=[key],
            overrides={key: True},
            effective_date="2030-01-01",
        )

    skipped = engine.plan_seed_refresh(selected_models=[key], overrides={key: False})
    assert skipped["insert_keys"] == []
    distinct = engine.plan_seed_refresh(
        selected_models=[key],
        overrides={key: True},
        version="2026.09-revised",
        effective_date="2030-01-01",
    )
    assert distinct["insert_keys"] == [key]


def test_refresh_preserves_old_date_version_rows_and_surfaces_metadata(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    old = _row(
        version="legacy-v1",
        effective_date="2026-01-01",
        provider="openai",
        model="gpt-4o-mini",
        input_rate=0.15,
        output_rate=0.60,
    )
    _legacy_db(db, [old])
    engine = CostEngine(str(db))
    plan = _plan_one(engine)
    assert plan["shadowing"]["openai/gpt-4o-mini"]
    assert engine.apply_seed_refresh(plan)["status"] == "applied"

    historical = engine.get_pricing("gpt-4o-mini", "2026-01-02")
    current = engine.get_pricing("gpt-4o-mini", "2026-09-05")
    assert historical.input_rate == pytest.approx(0.15)
    assert historical.unit_basis == UNKNOWN_METADATA
    assert current.input_rate == pytest.approx(0.00015)
    assert current.unit_basis == UNIT_BASIS_USD_PER_1K
    with engine._connect() as conn:
        by_version = engine._get_pricing_by_version(conn, "gpt-4o-mini", "legacy-v1")
    assert by_version is not None
    assert by_version.input_rate == pytest.approx(0.15)

    rows = engine.list_pricing()
    legacy_row = next(row for row in rows if row["version"] == "legacy-v1")
    fresh_row = next(row for row in rows if row["version"] == CURRENT_PRICING_VERSION)
    assert legacy_row["unit_basis"] == UNKNOWN_METADATA
    assert fresh_row["provenance"] == PROVENANCE_SEED_REFRESH


def test_stale_snapshot_refuses_without_partial_writes(tmp_path: Path) -> None:
    db = tmp_path / "stale.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    plan = _plan_one(engine)
    before = engine.list_pricing()
    engine.add_pricing("custom", "later-model", 2.0, 9.0, version="later-v1")

    with pytest.raises(StalePricingRefreshPlanError, match="changed after planning"):
        engine.apply_seed_refresh(plan)
    after = engine.list_pricing()
    assert len(after) == len(before) + 1
    assert not any(row["model"] == "gpt-4o-mini" for row in after)


def test_failed_multirow_apply_rolls_back_rows_and_receipt(tmp_path: Path) -> None:
    db = tmp_path / "rollback.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    keys = ["openai/gpt-4o-mini", "openai/gpt-5-nano"]
    plan = engine.plan_seed_refresh(
        selected_models=keys,
        overrides={key: True for key in keys},
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TRIGGER fail_second_seed BEFORE INSERT ON tp_pricing
               WHEN NEW.model = 'gpt-5-nano'
               BEGIN SELECT RAISE(ABORT, 'synthetic insert failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic insert failure"):
        engine.apply_seed_refresh(plan)
    with sqlite3.connect(db) as conn:
        inserted = conn.execute(
            "SELECT COUNT(*) FROM tp_pricing WHERE model IN ('gpt-4o-mini', 'gpt-5-nano')"
        ).fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM tp_pricing_refresh_receipts").fetchone()[0]
    assert inserted == 0
    assert receipts == 0


def test_exact_double_apply_is_safe_and_index_drift_refuses(tmp_path: Path) -> None:
    db = tmp_path / "double.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    plan = _plan_one(engine)
    first = engine.apply_seed_refresh(plan)
    second = engine.apply_seed_refresh(plan)
    assert first["status"] == "applied"
    assert second == {"status": "already_applied", "plan_hash": plan["plan_hash"], "inserted": 1}

    with sqlite3.connect(db) as conn:
        conn.execute("DROP INDEX idx_tp_pricing_unique")
        conn.execute(
            """CREATE UNIQUE INDEX idx_tp_pricing_unique
               ON tp_pricing(version, provider, model)
               WHERE provider <> ''"""
        )
    with pytest.raises(PricingRefreshUnavailableError, match="correctly shaped unique index"):
        engine.apply_seed_refresh(plan)


def test_double_apply_refuses_changed_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "catalog-drift.db"
    _legacy_db(db, [_row()])
    engine = CostEngine(str(db))
    plan = _plan_one(engine)
    engine.apply_seed_refresh(plan)
    target = next(row for row in cost_module.SEED_PRICING if row["model"] == "gpt-4o-mini")
    monkeypatch.setitem(target, "input_rate", float(target["input_rate"]) + 0.01)
    with pytest.raises(StalePricingRefreshPlanError, match="seed catalog changed"):
        engine.apply_seed_refresh(plan)


def test_two_engines_observe_committed_custom_pricing_without_restart(tmp_path: Path) -> None:
    db = tmp_path / "visibility.db"
    first = CostEngine(str(db))
    second = CostEngine(str(db))
    assert first.get_pricing("cross-process-model").provenance == PROVENANCE_FALLBACK
    second.add_pricing("custom", "cross-process-model", 0.25, 1.5, version="custom-v1")
    observed = first.get_pricing("cross-process-model")
    assert observed.input_rate == pytest.approx(0.25)
    assert observed.provenance == PROVENANCE_CUSTOM


def test_deterministic_exact_fuzzy_version_and_list_ties(tmp_path: Path) -> None:
    db = tmp_path / "ties.db"
    _legacy_db(
        db,
        [
            _row(model="model-alpha", input_rate=1.0),
            _row(model="model-alpha", input_rate=2.0),
            _row(model="model-alpha-long", input_rate=3.0),
        ],
    )
    engine = CostEngine(str(db))
    assert engine.get_pricing("model-alpha").input_rate == pytest.approx(2.0)
    assert engine.get_pricing("prefix-model-alpha-long-suffix").input_rate == pytest.approx(3.0)
    with engine._connect() as conn:
        by_version = engine._get_pricing_by_version(conn, "model-alpha", "legacy-v1")
    assert by_version is not None
    assert by_version.input_rate == pytest.approx(2.0)
    ids = [row["id"] for row in engine.list_pricing("legacy-v1") if row["model"] == "model-alpha"]
    assert ids == sorted(ids, reverse=True)


def test_submicro_cost_metadata_and_http_rates_surface(tmp_path: Path) -> None:
    db = tmp_path / "http.db"
    engine = CostEngine(str(db))
    result = engine.calculate("gpt-5-nano", 1, 1, 0)
    payload = result.to_dict()
    assert payload["actual_cost"] == pytest.approx(0.00000005)
    assert payload["actual_cost"] != 0
    assert payload["pricing_provenance"] == PROVENANCE_SEED_REFRESH
    assert payload["unit_basis"] == UNIT_BASIS_USD_PER_1K

    storage = TelemetryDB(db)
    with TestClient(create_app(db_path=str(db), storage=storage)) as client:
        response = client.get("/v1/pricing/rates")
    assert response.status_code == 200
    body = response.json()
    assert body["pricing"]
    assert all("provenance" in row and "unit_basis" in row for row in body["pricing"])


def test_refresh_leaves_cost_history_and_old_column_reads_untouched(tmp_path: Path) -> None:
    db = tmp_path / "cost-history.db"
    _legacy_db(db, [_row()])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE tp_costs (trace_id TEXT PRIMARY KEY, actual_cost REAL, cost_source TEXT)"
        )
        conn.execute("INSERT INTO tp_costs VALUES ('trace-1', 12.3456789, 'legacy')")
    engine = CostEngine(str(db))
    engine.apply_seed_refresh(_plan_one(engine))

    with sqlite3.connect(db) as conn:
        cost_row = conn.execute("SELECT * FROM tp_costs").fetchone()
        old_reader_rows = conn.execute(
            """SELECT version, effective_date, provider, model, input_rate,
                      output_rate, currency, source, created_at
               FROM tp_pricing ORDER BY id"""
        ).fetchall()
    assert cost_row == ("trace-1", 12.3456789, "legacy")
    assert len(old_reader_rows) == 2


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing reprocess pricing-version override is accepted but not used",
)
def test_reprocess_pricing_version_override_remains_a_documented_gap(tmp_path: Path) -> None:
    db = tmp_path / "reprocess.db"
    engine = CostEngine(str(db))
    engine.add_pricing(
        "openai",
        "gpt-4o-mini",
        9.0,
        9.0,
        version="override-v1",
        effective_date="2025-01-01",
    )
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tp_events (trace_id TEXT, model TEXT, ts TEXT, status TEXT)")
        conn.execute(
            "CREATE TABLE tp_usage (trace_id TEXT, input_billed INTEGER, input_est INTEGER, output_billed INTEGER)"
        )
        conn.execute(
            """CREATE TABLE tp_costs (
                   trace_id TEXT PRIMARY KEY, baseline_cost REAL, actual_cost REAL,
                   savings_total REAL, pricing_version TEXT, cost_source TEXT)"""
        )
        conn.execute("INSERT INTO tp_events VALUES ('trace-1', 'gpt-4o-mini', '2026-09-05', 'ok')")
        conn.execute("INSERT INTO tp_usage VALUES ('trace-1', 1000, 1000, 0)")
        conn.execute("INSERT INTO tp_costs VALUES ('trace-1', 0, 0, 0, 'old', 'old')")
    engine.reprocess_costs("2026-09-05", "2026-09-05", "override-v1")
    with sqlite3.connect(db) as conn:
        version = conn.execute(
            "SELECT pricing_version FROM tp_costs WHERE trace_id='trace-1'"
        ).fetchone()[0]
    assert version == "override-v1"
