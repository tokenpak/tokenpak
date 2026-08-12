"""Golden replay cases for deterministic session facts and state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.core.contracts.session_economics import (
    BurnSlope,
    CacheState,
    ForecastStatus,
    PriceFreshness,
    RateProvenance,
    RunwayStatus,
    SessionEconomics,
    ValueState,
)
from tokenpak.proxy.session_forecast import _build_session_economics
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

_COLUMNS = (
    "timestamp",
    "model",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "status_code",
    "cache_read_tokens",
    "cache_creation_tokens",
    "ttl_attribution",
    "session_id",
    "agent_id",
    "reasoning_effort",
    "provider_usage_ref",
    "provider_usage_provider",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_read_tokens",
    "provider_cache_creation_tokens",
    "provider_usage_source",
    "provider_usage_confidence",
    "cost_basis",
    "pricing_source",
)


def _create_ledger(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            status_code INTEGER,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER,
            ttl_attribution TEXT,
            session_id TEXT,
            agent_id TEXT,
            reasoning_effort TEXT,
            provider_usage_ref TEXT,
            provider_usage_provider TEXT,
            provider_input_tokens INTEGER,
            provider_output_tokens INTEGER,
            provider_cache_read_tokens INTEGER,
            provider_cache_creation_tokens INTEGER,
            provider_usage_source TEXT,
            provider_usage_confidence TEXT,
            cost_basis TEXT,
            pricing_source TEXT
        )"""
    )
    defaults: dict[str, object] = {
        "timestamp": "2026-08-10T12:00:00Z",
        "model": "claude-sonnet-4-5",
        "input_tokens": 100,
        "output_tokens": 20,
        "estimated_cost": 0.01,
        "status_code": 200,
        "cache_read_tokens": 10,
        "cache_creation_tokens": 5,
        "ttl_attribution": "5m",
        "session_id": "session-golden",
        "agent_id": "trix",
        "reasoning_effort": "high",
        "provider_usage_ref": "turn-default",
        "provider_usage_provider": "anthropic",
        # Anthropic declares that provider_input excludes cache classes;
        # 85 + 10 read + 5 write reconstructs a 100-token hot context.
        "provider_input_tokens": 85,
        "provider_output_tokens": 20,
        "provider_cache_read_tokens": 10,
        "provider_cache_creation_tokens": 5,
        "provider_usage_source": "provider_usage_object",
        "provider_usage_confidence": "high",
        "cost_basis": "provider_usage_rate_estimate",
        "pricing_source": "seed",
    }
    placeholders = ",".join("?" for _ in _COLUMNS)
    for overrides in rows:
        values = defaults | overrides
        conn.execute(
            f"INSERT INTO requests ({','.join(_COLUMNS)}) VALUES ({placeholders})",
            tuple(values[column] for column in _COLUMNS),
        )
    conn.commit()
    conn.close()
    return db


def _fresh_rates() -> RateProvenance:
    return RateProvenance(
        catalog_version="fixture-v1",
        effective_at="2026-08-01T00:00:00+00:00",
        source="offline golden fixture",
        freshness=PriceFreshness.FRESH,
    )


def _config(**overrides: object) -> SpendGuardConfig:
    config = SpendGuardConfig(
        warn_tokens=1_000_000,
        warn_cost_usd=0.0,
        rolling_caps_enabled=False,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_completed_turns_deduplicate_and_match_hand_calculated_ewmas(tmp_path: Path) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {"provider_usage_ref": "turn-1"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "input_tokens": 140,
                "output_tokens": 30,
                "estimated_cost": 0.02,
                "provider_input_tokens": 120,
                "provider_output_tokens": 30,
                "provider_cache_read_tokens": 20,
                "provider_cache_creation_tokens": 0,
            },
            {
                # Latest row wins for the duplicate provider event.
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "input_tokens": 140,
                "output_tokens": 30,
                "estimated_cost": 0.02,
                "provider_input_tokens": 120,
                "provider_output_tokens": 30,
                "provider_cache_read_tokens": 20,
                "provider_cache_creation_tokens": 0,
            },
            {
                "timestamp": "2026-08-10T12:02:00Z",
                "provider_usage_ref": "turn-3",
                "input_tokens": 180,
                "output_tokens": 40,
                "estimated_cost": 0.03,
                "provider_input_tokens": 150,
                "provider_output_tokens": 40,
                "provider_cache_read_tokens": 30,
                "provider_cache_creation_tokens": 0,
            },
            {
                # Not a completed request and therefore not a turn.
                "timestamp": "2026-08-10T12:02:30Z",
                "provider_usage_ref": "pending",
                "status_code": None,
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 3, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert economics.session.turns_observed == 3
    assert economics.session.model.id == "claude-sonnet-4-5"
    assert economics.facts.input_tokens.value == 355
    assert economics.facts.output_tokens.value == 90
    assert economics.facts.cache_read_tokens.value == 60
    assert economics.facts.cache_write_tokens.value == 5
    assert economics.facts.cost_usd.state is ValueState.ESTIMATED
    assert economics.facts.cost_usd.value == pytest.approx(0.06)

    assert economics.state.base_tokens.value == 100
    assert economics.state.context_tokens.value == 180
    assert economics.state.context_growth_ewma.value == 40
    # token burns 120, 170, 220 -> EWMA 120, 145, 182.5
    assert economics.state.burn_tokens_per_turn.value == pytest.approx(182.5)
    # USD burns .01, .02, .03 -> EWMA .01, .015, .0225
    assert economics.state.burn_usd_per_turn.value == pytest.approx(0.0225)
    assert economics.state.burn_slope is BurnSlope.UP
    assert economics.state.idle_seconds.value == 60
    assert economics.state.cache_ttl_seconds.value == 300
    assert economics.state.cache_state is CacheState.WARM
    assert economics.forecast.status is ForecastStatus.LEARNING
    assert economics.forecast.expected_turns.low is None
    assert economics.advisory is None

    encoded = economics.to_json()
    assert SessionEconomics.from_json(encoded).to_json() == encoded


def test_missing_provider_measurement_never_becomes_measured_zero(tmp_path: Path) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {"provider_usage_ref": "turn-1"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "provider_output_tokens": None,
                "provider_usage_source": "unavailable",
                "cost_basis": "subscription_billed_cost_unknown",
                "pricing_source": "unknown",
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
    )

    assert economics.facts.output_tokens.state is ValueState.UNAVAILABLE
    assert economics.facts.output_tokens.value is None
    assert economics.facts.cost_usd.state is ValueState.UNAVAILABLE
    assert economics.facts.cost_usd.value is None
    assert economics.state.context_tokens.state is ValueState.UNAVAILABLE
    assert economics.state.context_tokens.value is None
    assert economics.runway.status is RunwayStatus.UNAVAILABLE


def test_missing_and_malformed_costs_keep_distinct_truth_states(tmp_path: Path) -> None:
    missing_db = _create_ledger(
        tmp_path / "missing",
        [
            {"provider_usage_ref": "turn-1"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "estimated_cost": None,
            },
        ],
    )
    malformed_db = _create_ledger(
        tmp_path / "malformed",
        [
            {"provider_usage_ref": "turn-1"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "estimated_cost": -1.0,
            },
        ],
    )

    missing = _build_session_economics(
        "session-golden",
        monitor_db_path=str(missing_db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )
    malformed = _build_session_economics(
        "session-golden",
        monitor_db_path=str(malformed_db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert missing.facts.cost_usd.state is ValueState.UNAVAILABLE
    assert missing.state.burn_usd_per_turn.state is ValueState.UNAVAILABLE
    assert malformed.facts.cost_usd.state is ValueState.ERROR
    assert malformed.state.burn_usd_per_turn.state is ValueState.ERROR


def test_identical_usage_hash_on_different_timestamps_remains_two_turns(tmp_path: Path) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {"provider_usage_ref": "same-usage-content"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "same-usage-content",
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert economics.session.turns_observed == 2
    assert economics.facts.output_tokens.value == 40


@pytest.mark.parametrize(
    ("first_context", "second_context", "expected_slope", "expected_growth"),
    [
        (100, 104, BurnSlope.FLAT, 4.0),
        (200, 100, BurnSlope.DOWN, 0.0),
    ],
)
def test_burn_slope_tolerance_and_compaction_growth_floor(
    tmp_path: Path,
    first_context: int,
    second_context: int,
    expected_slope: BurnSlope,
    expected_growth: float,
) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {
                "provider_usage_ref": "turn-1",
                "input_tokens": first_context,
                "provider_input_tokens": first_context - 15,
            },
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "input_tokens": second_context,
                "provider_input_tokens": second_context - 15,
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert economics.state.burn_slope is expected_slope
    assert economics.state.context_growth_ewma.value == expected_growth


def test_provider_declared_cache_semantics_prevent_double_counting(tmp_path: Path) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {
                "model": "gpt-4.1",
                "provider_usage_ref": "turn-1",
                "provider_usage_provider": "openai",
                "provider_input_tokens": 100,
                "provider_output_tokens": 20,
                "provider_cache_read_tokens": 40,
                "provider_cache_creation_tokens": None,
            },
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "model": "gpt-4.1",
                "provider_usage_ref": "turn-2",
                "provider_usage_provider": "openai",
                "provider_input_tokens": 110,
                "provider_output_tokens": 20,
                "provider_cache_read_tokens": 50,
                "provider_cache_creation_tokens": None,
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert economics.state.base_tokens.value == 100
    assert economics.state.context_tokens.value == 110
    assert economics.state.context_growth_ewma.value == 10
    assert economics.facts.cache_read_tokens.value == 90
    assert economics.facts.cache_write_tokens.state is ValueState.UNAVAILABLE


def test_missing_identity_and_missing_ledger_are_explicit_no_data(tmp_path: Path) -> None:
    economics = _build_session_economics(
        "",
        monitor_db_path=str(tmp_path / "absent.db"),
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert economics.session.identity_state is ValueState.NO_DATA
    assert economics.session.id is None
    assert economics.facts.input_tokens.state is ValueState.NO_DATA
    assert economics.facts.input_tokens.value is None
    assert economics.runway.status is RunwayStatus.UNAVAILABLE


def test_corrupt_timestamp_is_an_error_not_an_empty_session(tmp_path: Path) -> None:
    db = _create_ledger(tmp_path, [{"timestamp": "not-a-timestamp"}])
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        spend_guard_config=_config(),
    )

    assert economics.facts.input_tokens.state is ValueState.ERROR
    assert economics.runway.status is RunwayStatus.ERROR
    assert "timestamp" in economics.runway.reason


def test_cache_ttl_state_expires_from_the_last_measured_ttl_turn(tmp_path: Path) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {"provider_usage_ref": "turn-1"},
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "input_tokens": 120,
                "provider_input_tokens": 105,
            },
        ],
    )
    economics = _build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=datetime(2026, 8, 10, 12, 7, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
    )

    assert economics.state.idle_seconds.value == 360
    assert economics.state.cache_ttl_seconds.value == 300
    assert economics.state.cache_state is CacheState.EXPIRED
