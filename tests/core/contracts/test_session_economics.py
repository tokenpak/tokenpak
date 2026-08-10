from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from tokenpak.core.contracts.session_economics import (
    SCHEMA_VERSION,
    BindingConstraint,
    BurnSlope,
    CacheState,
    CostBasis,
    CostValue,
    Coverage,
    DriftState,
    Forecast,
    ForecastStatus,
    GuardState,
    IntervalEstimate,
    ModelRef,
    NumericValue,
    PriceFreshness,
    RateProvenance,
    Runway,
    RunwayStatus,
    SessionEconomics,
    SessionEconomicsContractError,
    SessionFacts,
    SessionRef,
    SessionState,
    UnsupportedSessionEconomicsVersion,
    ValueState,
)


def _fresh_rate() -> RateProvenance:
    return RateProvenance(
        catalog_version="catalog-2026-08-09",
        effective_at="2026-08-09T00:00:00Z",
        source="provider-rate-card",
        freshness=PriceFreshness.FRESH,
    )


def _interval(low: float, high: float, *, unit: str) -> IntervalEstimate:
    return IntervalEstimate(
        ValueState.ESTIMATED,
        low,
        high,
        source="conformal-replay",
        unit=unit,
    )


def _available_contract() -> SessionEconomics:
    return SessionEconomics(
        as_of="2026-08-09T23:00:00Z",
        session=SessionRef(
            id="session-opaque",
            identity_state=ValueState.OBSERVED,
            turns_observed=12,
            model=ModelRef("provider/model", "high"),
        ),
        facts=SessionFacts(
            input_tokens=NumericValue.observed(12_000, source="provider-usage", unit="tokens"),
            output_tokens=NumericValue.observed(2_500, source="provider-usage", unit="tokens"),
            cache_read_tokens=NumericValue.observed(8_000, source="provider-usage", unit="tokens"),
            cache_write_tokens=NumericValue.observed(0, source="provider-usage", unit="tokens"),
            cost_usd=CostValue(
                ValueState.ESTIMATED,
                0.42,
                CostBasis.RATE_CARD,
                rate_provenance=_fresh_rate(),
            ),
        ),
        state=SessionState(
            context_tokens=NumericValue.observed(28_000, source="request-ledger", unit="tokens"),
            base_tokens=NumericValue.observed(12_000, source="request-ledger", unit="tokens"),
            context_growth_ewma=NumericValue.estimated(
                900, source="turnwise-ewma", unit="tokens/turn"
            ),
            burn_tokens_per_turn=NumericValue.estimated(
                1_200, source="turnwise-ewma", unit="tokens/turn"
            ),
            burn_usd_per_turn=NumericValue.estimated(
                0.035, source="fresh-rate-derived", unit="usd/turn"
            ),
            burn_slope=BurnSlope.UP,
            idle_seconds=NumericValue.observed(60, source="request-ledger", unit="seconds"),
            cache_ttl_seconds=NumericValue.observed(
                300, source="provider-cache-control", unit="seconds"
            ),
            cache_state=CacheState.WARM,
        ),
        runway=Runway(
            RunwayStatus.AVAILABLE,
            18,
            BindingConstraint.CONTEXT_SOFT,
            GuardState.AMBER,
        ),
        forecast=Forecast(
            status=ForecastStatus.AVAILABLE,
            remaining_tokens_likely_50=_interval(14_000, 22_000, unit="tokens"),
            remaining_tokens_ceiling_90=NumericValue.estimated(
                31_000, source="conformal-replay", unit="tokens"
            ),
            remaining_cost_usd_likely_50=_interval(0.45, 0.72, unit="usd"),
            remaining_cost_usd_ceiling_90=NumericValue.estimated(
                1.03, source="fresh-rate-derived", unit="usd"
            ),
            expected_turns=_interval(10, 18, unit="turns"),
            coverage=Coverage(
                method="adaptive-conformal",
                observed=0.51,
                history_n=40,
                drift_state=DriftState.STABLE,
            ),
            predicted_block_probability=NumericValue.estimated(
                0.2, source="held-out-replay", unit="probability"
            ),
        ),
    )


def test_contract_round_trips_without_state_loss() -> None:
    original = _available_contract()
    restored = SessionEconomics.from_json(original.to_json())

    assert restored == original
    assert restored.to_dict() == original.to_dict()
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.to_dict()["advisory"] is None


def test_serialization_is_stable_for_equivalent_values() -> None:
    first = _available_contract()
    second = SessionEconomics.from_dict(first.to_dict())
    assert first.to_json() == second.to_json()


def test_contract_objects_are_immutable() -> None:
    contract = _available_contract()
    with pytest.raises(FrozenInstanceError):
        contract.as_of = "2026-08-10T00:00:00Z"  # type: ignore[misc]


def test_observed_zero_is_not_missing() -> None:
    value = NumericValue.observed(0, source="provider-usage", unit="tokens")
    assert value.to_dict() == {
        "state": "observed",
        "value": 0,
        "source": "provider-usage",
        "unit": "tokens",
    }
    assert NumericValue.from_dict(value.to_dict()) == value


@pytest.mark.parametrize("state", [ValueState.NO_DATA, ValueState.UNAVAILABLE, ValueState.ERROR])
def test_non_numeric_states_reject_fallback_zero(state: ValueState) -> None:
    kwargs = {"reason": "failed"} if state is ValueState.ERROR else {}
    with pytest.raises(SessionEconomicsContractError, match="must serialize as null"):
        NumericValue(state, 0, **kwargs)


@pytest.mark.parametrize("state", [ValueState.OBSERVED, ValueState.ESTIMATED])
def test_numeric_states_require_value_and_source(state: ValueState) -> None:
    with pytest.raises(SessionEconomicsContractError):
        NumericValue(state, None, source="producer")
    with pytest.raises(SessionEconomicsContractError, match="source provenance"):
        NumericValue(state, 1)


def test_stale_rate_cannot_produce_numeric_usd() -> None:
    stale = RateProvenance(
        catalog_version="old",
        effective_at="2026-01-01T00:00:00Z",
        source="rate-card",
        freshness=PriceFreshness.STALE,
    )
    with pytest.raises(SessionEconomicsContractError, match="fresh rate provenance"):
        CostValue(
            ValueState.ESTIMATED,
            1.0,
            CostBasis.RATE_CARD,
            rate_provenance=stale,
        )

    unavailable = CostValue(
        ValueState.UNAVAILABLE,
        basis=CostBasis.RATE_CARD,
        reason="stale_rate",
        rate_provenance=stale,
    )
    assert unavailable.to_dict()["value"] is None
    assert unavailable.to_dict()["rate_provenance"]["freshness"] == "stale"


def test_absent_rate_cannot_produce_numeric_usd() -> None:
    with pytest.raises(SessionEconomicsContractError, match="complete rate provenance"):
        CostValue(ValueState.ESTIMATED, 1.0, CostBasis.RATE_CARD)


def test_tokens_continue_when_usd_is_unavailable() -> None:
    contract = _available_contract()
    unavailable_cost = CostValue(
        ValueState.UNAVAILABLE,
        basis=CostBasis.UNKNOWN,
        reason="price_provenance_unavailable",
    )
    state = replace(
        contract.state,
        burn_usd_per_turn=NumericValue.unavailable("price_provenance_unavailable"),
    )
    forecast = replace(
        contract.forecast,
        remaining_cost_usd_likely_50=IntervalEstimate(
            ValueState.UNAVAILABLE, reason="price_provenance_unavailable"
        ),
        remaining_cost_usd_ceiling_90=NumericValue.unavailable("price_provenance_unavailable"),
    )
    without_usd = replace(
        contract,
        facts=replace(contract.facts, cost_usd=unavailable_cost),
        state=state,
        forecast=forecast,
    )

    payload = without_usd.to_dict()
    assert payload["facts"]["input_tokens"]["value"] == 12_000
    assert payload["facts"]["cost_usd"]["value"] is None
    assert payload["forecast"]["remaining_cost_usd_ceiling_90"]["value"] is None


def test_numeric_remaining_usd_requires_fresh_rate_provenance() -> None:
    contract = _available_contract()
    unavailable_cost = CostValue(
        ValueState.UNAVAILABLE,
        basis=CostBasis.UNKNOWN,
        reason="price_provenance_unavailable",
    )
    with pytest.raises(SessionEconomicsContractError, match="remaining USD forecast"):
        replace(
            contract,
            facts=replace(contract.facts, cost_usd=unavailable_cost),
            state=replace(
                contract.state,
                burn_usd_per_turn=NumericValue.unavailable("price_provenance_unavailable"),
            ),
        )


def test_provider_bill_is_distinct_from_rate_estimate() -> None:
    billed = CostValue(
        ValueState.OBSERVED,
        2.5,
        CostBasis.PROVIDER_BILL,
        source="provider-invoice",
    )
    assert billed.to_dict()["state"] == "observed"
    assert billed.to_dict()["basis"] == "provider_bill"


def test_subscription_basis_cannot_claim_numeric_usd() -> None:
    with pytest.raises(SessionEconomicsContractError, match="provider_bill basis"):
        CostValue(
            ValueState.OBSERVED,
            2.5,
            CostBasis.SUBSCRIPTION,
            source="subscription",
        )


def test_unknown_additive_fields_are_ignored_by_v1_consumer() -> None:
    payload = _available_contract().to_dict()
    payload["future_top_level"] = {"enabled": True}
    payload["facts"]["input_tokens"]["future_provenance"] = "value"
    payload["forecast"]["coverage"]["future_metric"] = 0.9

    restored = SessionEconomics.from_dict(payload)

    assert "future_top_level" not in restored.to_dict()
    assert restored == _available_contract()


@pytest.mark.parametrize("version", [None, "session-economics/2", 1])
def test_schema_version_mismatch_is_explicit(version: object) -> None:
    payload = _available_contract().to_dict()
    payload["schema_version"] = version
    with pytest.raises(UnsupportedSessionEconomicsVersion, match="unsupported schema_version"):
        SessionEconomics.from_dict(payload)


def test_non_null_advisory_is_rejected() -> None:
    payload = _available_contract().to_dict()
    payload["advisory"] = {"route": "fresh-session"}
    with pytest.raises(SessionEconomicsContractError, match="non-null advisory"):
        SessionEconomics.from_dict(payload)


def test_missing_advisory_is_rejected() -> None:
    payload = _available_contract().to_dict()
    del payload["advisory"]
    with pytest.raises(SessionEconomicsContractError, match="explicit advisory: null"):
        SessionEconomics.from_dict(payload)


def test_missing_session_identity_remains_explicit() -> None:
    session = SessionRef(
        id=None,
        identity_state=ValueState.UNAVAILABLE,
        turns_observed=0,
        model=ModelRef("provider/model"),
        reason="missing_session_header",
    )
    assert session.to_dict()["id"] is None
    assert session.to_dict()["identity_state"] == "unavailable"


@pytest.mark.parametrize("turns", [True, 1.5, "2"])
def test_turn_count_requires_a_non_negative_integer(turns: object) -> None:
    with pytest.raises(SessionEconomicsContractError, match="non-negative integer"):
        SessionRef(
            id="session-opaque",
            identity_state=ValueState.OBSERVED,
            turns_observed=turns,  # type: ignore[arg-type]
            model=ModelRef("provider/model"),
        )


def test_available_forecast_requires_range_ceiling_and_turns() -> None:
    with pytest.raises(SessionEconomicsContractError, match="requires token range"):
        Forecast(
            status=ForecastStatus.AVAILABLE,
            remaining_tokens_likely_50=IntervalEstimate(ValueState.NO_DATA),
            remaining_tokens_ceiling_90=NumericValue.no_data(),
            remaining_cost_usd_likely_50=IntervalEstimate(ValueState.UNAVAILABLE),
            remaining_cost_usd_ceiling_90=NumericValue.unavailable(),
            expected_turns=IntervalEstimate(ValueState.NO_DATA),
            coverage=Coverage(),
            predicted_block_probability=NumericValue.no_data(),
        )


def test_token_ceiling_cannot_be_below_likely_range() -> None:
    forecast = _available_contract().forecast
    with pytest.raises(SessionEconomicsContractError, match="token ceiling"):
        replace(
            forecast,
            remaining_tokens_ceiling_90=NumericValue.estimated(
                21_999, source="conformal-replay", unit="tokens"
            ),
        )


def test_cost_ceiling_cannot_be_below_likely_range() -> None:
    forecast = _available_contract().forecast
    with pytest.raises(SessionEconomicsContractError, match="cost ceiling"):
        replace(
            forecast,
            remaining_cost_usd_ceiling_90=NumericValue.estimated(
                0.719, source="fresh-rate-derived", unit="usd"
            ),
        )


def test_hard_stop_cannot_report_positive_runway() -> None:
    runway = _available_contract().runway
    with pytest.raises(SessionEconomicsContractError, match="hard_stop runway"):
        replace(runway, guard_state=GuardState.HARD_STOP, turns=1)


@pytest.mark.parametrize(
    "coverage",
    [
        Coverage(observed=0.51, history_n=40),
        Coverage(method="adaptive-conformal", history_n=40),
        Coverage(method="adaptive-conformal", observed=0.51, history_n=0),
    ],
)
def test_available_forecast_requires_observed_coverage(coverage: Coverage) -> None:
    forecast = _available_contract().forecast
    with pytest.raises(SessionEconomicsContractError, match="observed coverage"):
        replace(forecast, coverage=coverage)


def test_unavailable_forecast_cannot_hide_cost_prediction() -> None:
    with pytest.raises(SessionEconomicsContractError, match="cannot carry predictions"):
        Forecast(
            status=ForecastStatus.UNAVAILABLE,
            remaining_tokens_likely_50=IntervalEstimate(ValueState.UNAVAILABLE),
            remaining_tokens_ceiling_90=NumericValue.unavailable(),
            remaining_cost_usd_likely_50=_interval(0.4, 0.8, unit="usd"),
            remaining_cost_usd_ceiling_90=NumericValue.estimated(
                1.0, source="fresh-rate-derived", unit="usd"
            ),
            expected_turns=IntervalEstimate(ValueState.UNAVAILABLE),
            coverage=Coverage(),
            predicted_block_probability=NumericValue.unavailable(),
        )


def test_rate_provenance_rejects_non_string_identity() -> None:
    with pytest.raises(SessionEconomicsContractError, match="must be a string or null"):
        RateProvenance(catalog_version=1)  # type: ignore[arg-type]


def test_numeric_provenance_rejects_structured_source() -> None:
    payload = _available_contract().to_dict()
    payload["facts"]["input_tokens"]["source"] = {"name": "provider-usage"}
    with pytest.raises(SessionEconomicsContractError, match="source must be a string"):
        SessionEconomics.from_dict(payload)


@pytest.mark.parametrize("field,value", [("id", 123), ("effort", ["high"]), ("effort", None)])
def test_model_reference_rejects_non_string_values(field: str, value: object) -> None:
    payload = _available_contract().to_dict()
    payload["session"]["model"][field] = value
    with pytest.raises(SessionEconomicsContractError, match=f"model.{field} must be a string"):
        SessionEconomics.from_dict(payload)


def test_rate_provenance_rejects_non_object_payload() -> None:
    payload = _available_contract().to_dict()
    payload["facts"]["cost_usd"]["rate_provenance"] = []
    with pytest.raises(SessionEconomicsContractError, match="rate_provenance must be an object"):
        SessionEconomics.from_dict(payload)


def test_as_of_requires_timezone() -> None:
    contract = _available_contract()
    payload = contract.to_dict()
    payload["as_of"] = "2026-08-09T23:00:00"
    with pytest.raises(SessionEconomicsContractError, match="include a timezone"):
        SessionEconomics.from_dict(payload)
