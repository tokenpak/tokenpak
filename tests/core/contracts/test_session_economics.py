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


class _EqualityMimic:
    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return other == self.value


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


@pytest.mark.parametrize("via_from_dict", [False, True])
def test_schema_version_rejects_non_string_equality_mimic(via_from_dict: bool) -> None:
    version = _EqualityMimic(SCHEMA_VERSION)
    with pytest.raises(UnsupportedSessionEconomicsVersion, match="unsupported schema_version"):
        if via_from_dict:
            payload = _available_contract().to_dict()
            payload["schema_version"] = version
            SessionEconomics.from_dict(payload)
        else:
            replace(_available_contract(), schema_version=version)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path,wire_value",
    [
        (("facts", "input_tokens", "state"), "observed"),
        (("facts", "cost_usd", "state"), "estimated"),
        (("facts", "cost_usd", "basis"), "rate_card"),
        (("facts", "cost_usd", "rate_provenance", "freshness"), "fresh"),
        (("session", "identity_state"), "observed"),
        (("state", "burn_slope"), "up"),
        (("state", "cache_state"), "warm"),
        (("runway", "status"), "available"),
        (("runway", "binding_constraint"), "budget"),
        (("runway", "guard_state"), "amber"),
        (("forecast", "status"), "available"),
        (("forecast", "remaining_tokens_likely_50", "state"), "estimated"),
        (("forecast", "coverage", "drift_state"), "stable"),
    ],
)
def test_from_dict_rejects_non_string_enum_equality_mimics(
    path: tuple[str, ...], wire_value: str
) -> None:
    payload = _available_contract().to_dict()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = _EqualityMimic(wire_value)

    with pytest.raises(SessionEconomicsContractError, match="must be one of"):
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


@pytest.mark.parametrize(
    "field",
    ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"],
)
def test_session_facts_reject_estimated_token_values(field: str) -> None:
    facts = _available_contract().facts
    with pytest.raises(SessionEconomicsContractError, match=f"facts.{field} cannot use estimated"):
        replace(
            facts,
            **{field: NumericValue.estimated(1, source="synthetic", unit="tokens")},
        )


@pytest.mark.parametrize(
    "factory,match",
    [
        (lambda: NumericValue.observed(1, source=" \t"), "source must not be whitespace-only"),
        (lambda: ModelRef(" \t"), "model.id must be non-empty"),
        (lambda: ModelRef("provider/model", " \t"), "model.effort must be non-empty"),
        (
            lambda: SessionRef(" \t", ValueState.OBSERVED, 1, ModelRef("provider/model")),
            "observed session identity requires a non-empty id",
        ),
        (
            lambda: RateProvenance(
                catalog_version=" \t",
                effective_at="2026-08-09T00:00:00Z",
                source="provider-rate-card",
                freshness=PriceFreshness.FRESH,
            ),
            "catalog_version must be non-empty",
        ),
        (
            lambda: RateProvenance(
                catalog_version="catalog-1",
                effective_at="2026-08-09T00:00:00Z",
                source=" \t",
                freshness=PriceFreshness.FRESH,
            ),
            "source must be non-empty",
        ),
        (
            lambda: CostValue(
                ValueState.OBSERVED,
                1,
                CostBasis.PROVIDER_BILL,
                source=" \t",
            ),
            "cost_usd.source must not be whitespace-only",
        ),
        (
            lambda: IntervalEstimate(ValueState.ESTIMATED, 1, 2, source=" \t"),
            "interval.source must not be whitespace-only",
        ),
        (lambda: Coverage(method=" \t"), "coverage.method must be non-empty"),
    ],
)
def test_whitespace_only_identifiers_are_rejected(factory, match: str) -> None:
    with pytest.raises(SessionEconomicsContractError, match=match):
        factory()


@pytest.mark.parametrize(
    "format_only",
    [
        "\u034f",
        "\u115f",
        "\u1160",
        "\u180b",
        "\u200b",
        "\u2800",
        "\u3164",
        "\ufeff",
        "\uffa0",
        "\U000e0100",
        "\u200b\ufeff",
    ],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda value: NumericValue.observed(1, source=value),
        lambda value: NumericValue.error(value),
        lambda value: ModelRef(value),
        lambda value: ModelRef("provider/model", value),
        lambda value: SessionRef(
            value,
            ValueState.OBSERVED,
            1,
            ModelRef("provider/model"),
        ),
        lambda value: RateProvenance(
            catalog_version=value,
            effective_at="2026-08-09T00:00:00Z",
            source="provider-rate-card",
            freshness=PriceFreshness.FRESH,
        ),
        lambda value: RateProvenance(
            catalog_version="catalog-1",
            effective_at="2026-08-09T00:00:00Z",
            source=value,
            freshness=PriceFreshness.FRESH,
        ),
        lambda value: CostValue(
            ValueState.OBSERVED,
            1,
            CostBasis.PROVIDER_BILL,
            source=value,
        ),
        lambda value: IntervalEstimate(ValueState.ESTIMATED, 1, 2, source=value),
        lambda value: Coverage(method=value),
    ],
)
def test_format_only_identifiers_are_rejected(factory, format_only: str) -> None:
    with pytest.raises(SessionEconomicsContractError):
        factory(format_only)


def test_isolated_surrogate_is_rejected_from_truth_bearing_strings() -> None:
    with pytest.raises(SessionEconomicsContractError, match="valid Unicode scalar values"):
        RateProvenance(
            catalog_version="\ud800",
            effective_at="2026-08-09T00:00:00Z",
            source="provider-rate-card",
            freshness=PriceFreshness.FRESH,
        )


@pytest.mark.parametrize(
    "visible",
    ["café", "cafe\u0301", "モデル", "model\u200b", "🚀\ufe0f"],
)
def test_visible_unicode_identifiers_are_preserved_without_normalization(visible: str) -> None:
    reference = ModelRef(visible)
    assert reference.id == visible
    assert reference.to_dict()["id"] == visible


@pytest.mark.parametrize(
    "factory,field",
    [
        (lambda: NumericValue("bogus"), "numeric value.state"),
        (lambda: RateProvenance(freshness="bogus"), "rate_provenance.freshness"),
        (lambda: CostValue("bogus"), "cost_usd.state"),
        (
            lambda: CostValue(ValueState.UNAVAILABLE, basis="bogus"),
            "cost_usd.basis",
        ),
        (
            lambda: SessionRef(None, "bogus", 0, ModelRef("provider/model")),
            "session.identity_state",
        ),
        (lambda: replace(_available_contract().state, burn_slope="bogus"), "state.burn_slope"),
        (lambda: replace(_available_contract().state, cache_state="bogus"), "state.cache_state"),
        (
            lambda: Runway(
                "bogus",
                None,
                BindingConstraint.UNKNOWN,
                GuardState.UNKNOWN,
            ),
            "runway.status",
        ),
        (
            lambda: Runway(
                RunwayStatus.UNAVAILABLE,
                None,
                "bogus",
                GuardState.UNKNOWN,
            ),
            "runway.binding_constraint",
        ),
        (
            lambda: Runway(
                RunwayStatus.UNAVAILABLE,
                None,
                BindingConstraint.UNKNOWN,
                "bogus",
            ),
            "runway.guard_state",
        ),
        (lambda: IntervalEstimate("bogus"), "interval.state"),
        (lambda: Coverage(drift_state="bogus"), "forecast.coverage.drift_state"),
        (lambda: replace(_available_contract().forecast, status="bogus"), "forecast.status"),
    ],
)
def test_direct_construction_rejects_unknown_enum_members(factory, field: str) -> None:
    with pytest.raises(SessionEconomicsContractError, match=field):
        factory()


@pytest.mark.parametrize(
    "value_object",
    [
        NumericValue,
        RateProvenance,
        CostValue,
        SessionFacts,
        ModelRef,
        SessionRef,
        SessionState,
        Runway,
        IntervalEstimate,
        Coverage,
        Forecast,
        SessionEconomics,
    ],
)
def test_contract_value_objects_cannot_bypass_validation_by_subclassing(
    value_object: type,
) -> None:
    with pytest.raises(TypeError, match="does not support subclassing"):
        type(f"Forged{value_object.__name__}", (value_object,), {})


def test_rate_card_cost_rejects_duck_typed_provenance() -> None:
    class ForgedRate:
        complete = True
        freshness = PriceFreshness.FRESH

        def to_dict(self) -> dict[str, object]:
            return {
                "catalog_version": None,
                "effective_at": None,
                "source": None,
                "freshness": "fresh",
            }

    with pytest.raises(SessionEconomicsContractError, match="validated RateProvenance"):
        CostValue(
            ValueState.ESTIMATED,
            1,
            CostBasis.RATE_CARD,
            rate_provenance=ForgedRate(),  # type: ignore[arg-type]
        )


def test_session_facts_rejects_duck_typed_numeric_value() -> None:
    class ForgedNumeric:
        state = ValueState.OBSERVED

        def to_dict(self) -> dict[str, object]:
            return {"state": "observed", "value": 1, "source": "forged"}

    facts = _available_contract().facts
    with pytest.raises(SessionEconomicsContractError, match="validated NumericValue"):
        replace(facts, input_tokens=ForgedNumeric())  # type: ignore[arg-type]


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
