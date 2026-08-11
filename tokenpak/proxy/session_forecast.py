# SPDX-License-Identifier: Apache-2.0
"""Deterministic session facts, burn, cache state, and binding runway.

This module consumes completed rows from ``monitor.db.requests``.  It does
not predict remaining task length, compare routes, recommend a fresh session,
or call a provider.  Missing identity, measurements, timestamps, and pricing
remain explicit contract states rather than numeric zeroes.

Operational definitions are deliberately small and hand-checkable:

* one turn is one completed ledger event; a repeated non-empty
  ``provider_usage_ref`` collapses only when the ledger timestamp also
  matches, because the reference hashes usage content rather than request
  identity;
* provider input plus declared cache classes is the normalized hot context
  ``C`` (providers whose input already includes cache are not double-counted);
* the first completed request's normalized input is the session baseline
  ``B`` (not a claim that system/tools were separately measured);
* token burn is normalized input plus output for each turn;
* context growth is ``max(0, C[t] - C[t-1])``;
* EWMA alpha is 0.5, and burn slope changes only beyond a 5% tolerance;
* runway is the minimum whole-turn distance to every active measurable
  Spend Guard/configured constraint.  A known hard stop always wins.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from typing import Mapping, Sequence

from tokenpak.core.contracts.session_economics import (
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
    SessionFacts,
    SessionRef,
    SessionState,
    ValueState,
)
from tokenpak.proxy.spend_guard._context_window import get_model_max_context
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig, load_config
from tokenpak.proxy.spend_guard.session_state import (
    _read_completed_session_rows,
    _SessionLedgerRead,
    _SessionLedgerRow,
)

_EWMA_ALPHA = 0.5
_SLOPE_RELATIVE_TOLERANCE = 0.05
_CACHE_TTL_SECONDS = {"5m": 300, "1h": 3600, "mixed": 300}
_LEDGER_SOURCE = "monitor.db.requests"
_AUTO_ROLLING_USAGE = object()
logger = logging.getLogger(__name__)


class _SessionForecastDataError(ValueError):
    """Raised internally when recorded ledger values are corrupt."""


@dataclass(frozen=True)
class _Turn:
    row: _SessionLedgerRow
    at: datetime


@dataclass(frozen=True)
class _RunwayCandidate:
    turns: int
    binding: BindingConstraint
    order: int
    hard_stop: bool = False


def _now_utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise _SessionForecastDataError("as-of time must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _SessionForecastDataError("completed request timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise _SessionForecastDataError(f"invalid completed request timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        # Monitor historically writes local wall-clock ISO strings.  Attach
        # the host's local zone for the recorded date (including its DST
        # offset) instead of using today's fixed offset or assuming UTC.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _SessionForecastDataError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _SessionForecastDataError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise _SessionForecastDataError(f"{field} must be finite and non-negative")
    return result


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _deduplicate_rows(rows: Sequence[_SessionLedgerRow]) -> tuple[_Turn, ...]:
    """Deduplicate exact provider-observation/timestamp event identities."""
    keyed: dict[tuple[str, datetime], _Turn] = {}
    unkeyed: list[_Turn] = []
    seen_ids: set[int] = set()
    for row in rows:
        row_id = _nonnegative_int(row.id, "requests.id")
        if row_id in seen_ids:
            raise _SessionForecastDataError(f"duplicate requests.id {row_id}")
        seen_ids.add(row_id)
        turn = _Turn(row=row, at=_parse_timestamp(row.timestamp))
        provider_ref = _text(row.provider_usage_ref)
        if not provider_ref:
            unkeyed.append(turn)
            continue
        event_key = (provider_ref, turn.at)
        prior = keyed.get(event_key)
        if prior is None or (turn.at, row_id) > (prior.at, prior.row.id):
            keyed[event_key] = turn
    return tuple(sorted((*unkeyed, *keyed.values()), key=lambda turn: (turn.at, turn.row.id)))


def _provider_measurement_is_observed(row: _SessionLedgerRow) -> bool:
    source = _text(row.provider_usage_source).lower()
    return bool(source and source not in {"estimated", "unavailable", "unknown", "error"})


def _fact_total(
    turns: Sequence[_Turn],
    field: str,
    *,
    unit: str = "tokens",
) -> NumericValue:
    if not turns:
        return NumericValue.no_data("session has no completed turns", unit=unit)
    if any(not _provider_measurement_is_observed(turn.row) for turn in turns):
        return NumericValue.unavailable(
            f"{field} is not provider-observed for every completed turn",
            unit=unit,
        )
    raw_values = [getattr(turn.row, field) for turn in turns]
    if any(value is None for value in raw_values):
        return NumericValue.unavailable(
            f"{field} is missing for one or more completed turns",
            unit=unit,
        )
    try:
        values = [_nonnegative_int(value, f"requests.{field}") for value in raw_values]
    except _SessionForecastDataError as exc:
        return NumericValue.error(str(exc), unit=unit)
    confidences = {_text(turn.row.provider_usage_confidence) for turn in turns}
    confidences.discard("")
    confidence = next(iter(confidences)) if len(confidences) == 1 else "mixed"
    return NumericValue.observed(
        sum(values),
        source=f"{_LEDGER_SOURCE}.{field}",
        confidence=confidence,
        unit=unit,
    )


def _catalog_provenance(turns: Sequence[_Turn], as_of: datetime) -> RateProvenance:
    """Resolve bundled-catalog provenance without asserting source-class freshness."""
    if not turns or any(_text(turn.row.pricing_source).lower() != "seed" for turn in turns):
        return RateProvenance(freshness=PriceFreshness.UNKNOWN)
    try:
        from tokenpak.telemetry.pricing import _STALENESS_DAYS, PricingCatalog

        catalog = PricingCatalog.load()
        models = {_text(turn.row.model) for turn in turns}
        if not models or "" in models or any(catalog.get_model(model) is None for model in models):
            return RateProvenance(freshness=PriceFreshness.UNKNOWN)
        if not catalog.version or not catalog.updated:
            return RateProvenance(freshness=PriceFreshness.UNKNOWN)
        updated = date.fromisoformat(catalog.updated)
        age_days = (as_of.date() - updated).days
        freshness = (
            PriceFreshness.FRESH if 0 <= age_days <= _STALENESS_DAYS else PriceFreshness.STALE
        )
        effective_at = datetime.combine(updated, datetime_time.min, tzinfo=timezone.utc).isoformat()
        return RateProvenance(
            catalog_version=catalog.version,
            effective_at=effective_at,
            source="tokenpak bundled pricing catalog",
            freshness=freshness,
        )
    except (OSError, TypeError, ValueError):
        return RateProvenance(freshness=PriceFreshness.UNKNOWN)


def _cost_value(
    turns: Sequence[_Turn],
    *,
    as_of: datetime,
    rate_provenance: RateProvenance | None,
) -> tuple[CostValue, list[float] | None]:
    if not turns:
        return (
            CostValue(
                state=ValueState.NO_DATA,
                basis=CostBasis.UNKNOWN,
                reason="session has no completed turns",
            ),
            None,
        )
    bases = {_text(turn.row.cost_basis).lower() for turn in turns}
    if any("subscription" in basis for basis in bases):
        return (
            CostValue(
                state=ValueState.UNAVAILABLE,
                basis=CostBasis.SUBSCRIPTION,
                reason="subscription-billed requests do not expose provider-billed USD",
            ),
            None,
        )
    raw_costs = [turn.row.estimated_cost for turn in turns]
    if any(value is None for value in raw_costs):
        basis = (
            CostBasis.PROVIDER_BILL
            if bases == {"provider_bill"}
            else CostBasis.RATE_CARD
            if bases == {"provider_usage_rate_estimate"}
            else CostBasis.UNKNOWN
        )
        return (
            CostValue(
                state=ValueState.UNAVAILABLE,
                basis=basis,
                reason="estimated_cost is missing for one or more completed turns",
            ),
            None,
        )
    try:
        costs = [_nonnegative_number(value, "requests.estimated_cost") for value in raw_costs]
    except _SessionForecastDataError as exc:
        return (
            CostValue(
                state=ValueState.ERROR,
                basis=CostBasis.UNKNOWN,
                reason=str(exc),
            ),
            None,
        )

    if bases == {"provider_bill"}:
        return (
            CostValue(
                state=ValueState.OBSERVED,
                value=sum(costs),
                basis=CostBasis.PROVIDER_BILL,
                source=f"{_LEDGER_SOURCE}.estimated_cost:provider_bill",
            ),
            costs,
        )

    if bases == {"provider_usage_rate_estimate"}:
        provenance = rate_provenance or _catalog_provenance(turns, as_of)
        if provenance.complete and provenance.freshness is PriceFreshness.FRESH:
            return (
                CostValue(
                    state=ValueState.ESTIMATED,
                    value=sum(costs),
                    basis=CostBasis.RATE_CARD,
                    source=f"{_LEDGER_SOURCE}.estimated_cost",
                    rate_provenance=provenance,
                ),
                costs,
            )
        freshness = provenance.freshness.value
        return (
            CostValue(
                state=ValueState.UNAVAILABLE,
                basis=CostBasis.RATE_CARD,
                reason=f"complete fresh rate provenance is unavailable ({freshness})",
                rate_provenance=provenance,
            ),
            None,
        )

    return (
        CostValue(
            state=ValueState.UNAVAILABLE,
            basis=CostBasis.UNKNOWN,
            reason="completed turns do not share a supported cost basis",
        ),
        None,
    )


def _normalized_counts(turn: _Turn) -> tuple[int, int] | None:
    if not _provider_measurement_is_observed(turn.row):
        return None
    if turn.row.provider_input_tokens is None or turn.row.provider_output_tokens is None:
        return None
    try:
        from tokenpak.services.providers._registry import get_input_tokens_include_cache

        provider = _text(turn.row.provider_usage_provider)
        includes_cache = get_input_tokens_include_cache(provider)
        if includes_cache is None:
            return None
        input_tokens = _nonnegative_int(
            turn.row.provider_input_tokens,
            "requests.provider_input_tokens",
        )
        output_tokens = _nonnegative_int(
            turn.row.provider_output_tokens,
            "requests.provider_output_tokens",
        )
        if not includes_cache:
            input_tokens += _nonnegative_int(
                turn.row.provider_cache_read_tokens,
                "requests.provider_cache_read_tokens",
            )
            input_tokens += _nonnegative_int(
                turn.row.provider_cache_creation_tokens,
                "requests.provider_cache_creation_tokens",
            )
    except _SessionForecastDataError:
        return None
    return input_tokens, output_tokens


def _ewma(values: Sequence[float]) -> float:
    if not values:
        raise _SessionForecastDataError("EWMA requires at least one value")
    current = float(values[0])
    for value in values[1:]:
        current = _EWMA_ALPHA * float(value) + (1.0 - _EWMA_ALPHA) * current
    return current


def _ewma_series(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    result = [float(values[0])]
    for value in values[1:]:
        result.append(_EWMA_ALPHA * float(value) + (1.0 - _EWMA_ALPHA) * result[-1])
    return result


def _slope(values: Sequence[float]) -> BurnSlope:
    series = _ewma_series(values)
    if len(series) < 2:
        return BurnSlope.UNKNOWN
    prior, current = series[-2], series[-1]
    tolerance = max(1.0, abs(prior) * _SLOPE_RELATIVE_TOLERANCE)
    if current > prior + tolerance:
        return BurnSlope.UP
    if current < prior - tolerance:
        return BurnSlope.DOWN
    return BurnSlope.FLAT


def _state_values(
    turns: Sequence[_Turn],
    *,
    as_of: datetime,
    cost: CostValue,
    cost_series: Sequence[float] | None,
) -> tuple[SessionState, list[float] | None, list[float] | None, list[float] | None]:
    normalized = [_normalized_counts(turn) for turn in turns]
    if not turns:
        missing = lambda unit: NumericValue.no_data(  # noqa: E731
            "session has no completed turns", unit=unit
        )
        return (
            SessionState(
                context_tokens=missing("tokens"),
                base_tokens=missing("tokens"),
                context_growth_ewma=missing("tokens/turn"),
                burn_tokens_per_turn=missing("tokens/turn"),
                burn_usd_per_turn=missing("usd/turn"),
                burn_slope=BurnSlope.UNKNOWN,
                idle_seconds=missing("seconds"),
                cache_ttl_seconds=missing("seconds"),
                cache_state=CacheState.UNKNOWN,
            ),
            None,
            None,
            None,
        )

    if any(value is None for value in normalized):
        unavailable = lambda unit: NumericValue.unavailable(  # noqa: E731
            "normalized provider token counts are incomplete", unit=unit
        )
        contexts: list[float] | None = None
        token_burns: list[float] | None = None
        context_tokens = unavailable("tokens")
        base_tokens = unavailable("tokens")
        growth_value = unavailable("tokens/turn")
        burn_tokens_value = unavailable("tokens/turn")
        burn_slope = BurnSlope.UNKNOWN
    else:
        pairs = [value for value in normalized if value is not None]
        contexts = [float(value[0]) for value in pairs]
        token_burns = [float(value[0] + value[1]) for value in pairs]
        context_tokens = NumericValue.observed(
            int(contexts[-1]),
            source=f"{_LEDGER_SOURCE}.provider_usage:normalized_input:last",
            unit="tokens",
        )
        base_tokens = NumericValue.observed(
            int(contexts[0]),
            source=f"{_LEDGER_SOURCE}.provider_usage:normalized_input:first",
            unit="tokens",
        )
        if len(turns) < 2:
            growth_value = NumericValue.no_data(
                "at least two completed turns are required", unit="tokens/turn"
            )
            burn_tokens_value = NumericValue.no_data(
                "at least two completed turns are required", unit="tokens/turn"
            )
            burn_slope = BurnSlope.UNKNOWN
        else:
            growth = [
                max(0.0, contexts[index] - contexts[index - 1]) for index in range(1, len(contexts))
            ]
            growth_value = NumericValue.observed(
                _ewma(growth),
                source=f"{_LEDGER_SOURCE}.provider_usage:positive_context_delta_ewma",
                unit="tokens/turn",
            )
            burn_tokens_value = NumericValue.observed(
                _ewma(token_burns),
                source=f"{_LEDGER_SOURCE}.provider_usage:normalized_total_ewma",
                unit="tokens/turn",
            )
            burn_slope = _slope(token_burns)

    if len(turns) < 2:
        burn_usd = NumericValue.no_data(
            "at least two completed turns are required", unit="usd/turn"
        )
    elif cost_series is None:
        if cost.state is ValueState.ERROR:
            burn_usd = NumericValue.error(
                cost.reason or "USD measurement failed",
                unit="usd/turn",
            )
        else:
            burn_usd = NumericValue.unavailable(
                cost.reason or "USD measurements are unavailable", unit="usd/turn"
            )
    else:
        burn_state = cost.state
        burn_usd = NumericValue(
            state=burn_state,
            value=_ewma(cost_series),
            source=f"{_LEDGER_SOURCE}.estimated_cost:ewma",
            unit="usd/turn",
        )

    idle = (as_of - turns[-1].at).total_seconds()
    if idle < -1.0:
        raise _SessionForecastDataError("latest completed request timestamp is in the future")
    idle_value = NumericValue.observed(
        max(0.0, idle), source=f"{_LEDGER_SOURCE}.timestamp:last", unit="seconds"
    )

    cache_anchor: _Turn | None = None
    ttl_seconds: int | None = None
    for turn in reversed(turns):
        attribution = _text(turn.row.ttl_attribution).lower()
        if attribution in _CACHE_TTL_SECONDS:
            cache_anchor = turn
            ttl_seconds = _CACHE_TTL_SECONDS[attribution]
            break
    if cache_anchor is None or ttl_seconds is None:
        ttl_value = NumericValue.no_data(
            "no completed turn carries a measurable cache TTL", unit="seconds"
        )
        cache_state = CacheState.UNKNOWN
    else:
        ttl_value = NumericValue.observed(
            ttl_seconds,
            source=f"{_LEDGER_SOURCE}.ttl_attribution",
            unit="seconds",
        )
        cache_age = max(0.0, (as_of - cache_anchor.at).total_seconds())
        cache_state = CacheState.WARM if cache_age <= ttl_seconds else CacheState.EXPIRED

    cache_burns: list[float] | None = None
    raw_cache = [turn.row.provider_cache_read_tokens for turn in turns]
    if (
        len(turns) >= 2
        and all(_provider_measurement_is_observed(turn.row) for turn in turns)
        and all(value is not None for value in raw_cache)
    ):
        try:
            cache_burns = [
                float(_nonnegative_int(value, "requests.provider_cache_read_tokens"))
                for value in raw_cache
            ]
        except _SessionForecastDataError:
            cache_burns = None

    return (
        SessionState(
            context_tokens=context_tokens,
            base_tokens=base_tokens,
            context_growth_ewma=growth_value,
            burn_tokens_per_turn=burn_tokens_value,
            burn_usd_per_turn=burn_usd,
            burn_slope=burn_slope,
            idle_seconds=idle_value,
            cache_ttl_seconds=ttl_value,
            cache_state=cache_state,
        ),
        contexts,
        token_burns,
        cache_burns,
    )


def _turns_until(limit: float, used: float, burn: float) -> int | None:
    if used >= limit:
        return 0
    if burn <= 0:
        return None
    return max(0, int(math.ceil((limit - used) / burn)))


def _rolling_value(usage: Mapping[str, object], key: str) -> float:
    if key not in usage:
        raise _SessionForecastDataError(f"rolling usage is missing {key}")
    return _nonnegative_number(usage[key], f"rolling_usage.{key}")


def _resolve_runway(
    turns: Sequence[_Turn],
    state: SessionState,
    cost: CostValue,
    cost_series: Sequence[float] | None,
    contexts: Sequence[float] | None,
    token_burns: Sequence[float] | None,
    cache_burns: Sequence[float] | None,
    *,
    as_of: datetime,
    config: SpendGuardConfig,
    monitor_db_path: str | None,
    rolling_usage: object,
) -> Runway:
    if not turns:
        return Runway(
            status=RunwayStatus.UNAVAILABLE,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason="session has no completed turns",
        )
    if not config.enabled:
        return Runway(
            status=RunwayStatus.UNAVAILABLE,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason="Spend Guard constraints are disabled",
        )

    candidates: list[_RunwayCandidate] = []
    unavailable: list[str] = []
    learning: list[str] = []
    context_current = contexts[-1] if contexts else None
    context_growth = (
        float(state.context_growth_ewma.value)
        if state.context_growth_ewma.state is ValueState.OBSERVED
        else None
    )
    token_current = token_burns[-1] if token_burns else None
    token_growth = (
        _ewma(
            [
                max(0.0, token_burns[index] - token_burns[index - 1])
                for index in range(1, len(token_burns))
            ]
        )
        if token_burns is not None and len(token_burns) >= 2
        else None
    )
    token_burn = (
        float(state.burn_tokens_per_turn.value)
        if state.burn_tokens_per_turn.state is ValueState.OBSERVED
        else None
    )
    usd_burn = (
        float(state.burn_usd_per_turn.value)
        if state.burn_usd_per_turn.state in {ValueState.OBSERVED, ValueState.ESTIMATED}
        else None
    )
    latest_cost = cost_series[-1] if cost_series else None
    model = _text(turns[-1].row.model)
    model_context = get_model_max_context(model or None)

    def add(
        limit: float,
        used: float,
        burn: float,
        binding: BindingConstraint,
        order: int,
        *,
        hard: bool = False,
    ) -> None:
        remaining = _turns_until(limit, used, burn)
        if remaining is not None:
            candidates.append(_RunwayCandidate(remaining, binding, order, hard_stop=hard))

    # Context/legacy token planes mirror Standard 29's current model-aware
    # threshold choice.  Hard-stop candidates are evaluated first below.
    if model_context is not None and model_context > 0:
        soft = model_context * config.default_context_window_percent / 100.0
        hard = model_context * config.hard_stop_context_window_percent / 100.0
        if context_current is None:
            unavailable.append("context measurement is unavailable")
        elif context_current >= hard:
            candidates.append(
                _RunwayCandidate(0, BindingConstraint.CONTEXT_HARD, 0, hard_stop=True)
            )
        elif context_current >= soft:
            candidates.append(_RunwayCandidate(0, BindingConstraint.CONTEXT_SOFT, 20))
        elif context_growth is None:
            learning.append("context growth needs at least two completed turns")
        else:
            add(
                hard, context_current, context_growth, BindingConstraint.CONTEXT_HARD, 10, hard=True
            )
            add(soft, context_current, context_growth, BindingConstraint.CONTEXT_SOFT, 20)
    else:
        if token_current is None:
            unavailable.append("fallback request-token measurement is unavailable")
        elif token_current >= config.hard_block_tokens > 0:
            candidates.append(
                _RunwayCandidate(0, BindingConstraint.CONTEXT_HARD, 1, hard_stop=True)
            )
        elif token_current >= config.block_tokens > 0:
            candidates.append(_RunwayCandidate(0, BindingConstraint.CONTEXT_SOFT, 21))
        elif token_growth is None:
            learning.append("request-token growth needs at least two completed turns")
        else:
            if config.hard_block_tokens > 0:
                add(
                    float(config.hard_block_tokens),
                    token_current,
                    token_growth,
                    BindingConstraint.CONTEXT_HARD,
                    11,
                    hard=True,
                )
            if config.block_tokens > 0:
                add(
                    float(config.block_tokens),
                    token_current,
                    token_growth,
                    BindingConstraint.CONTEXT_SOFT,
                    21,
                )

    per_request_cost_constraints = any(
        value > 0 for value in (config.hard_block_cost_usd, config.block_cost_usd)
    )
    cost_constraints = any(
        value > 0
        for value in (
            config.hard_block_cost_usd,
            config.block_cost_usd,
            config.session_block_cost_usd,
        )
    )
    if cost_constraints and (latest_cost is None or usd_burn is None):
        unavailable.append("active dollar guard lacks complete fresh pricing")
    elif latest_cost is not None and usd_burn is not None:
        per_request_cost_is_binding = False
        if config.hard_block_cost_usd > 0 and latest_cost >= config.hard_block_cost_usd:
            candidates.append(_RunwayCandidate(0, BindingConstraint.BUDGET, 2, hard_stop=True))
            per_request_cost_is_binding = True
        if config.block_cost_usd > 0 and latest_cost >= config.block_cost_usd:
            candidates.append(_RunwayCandidate(0, BindingConstraint.BUDGET, 22))
            per_request_cost_is_binding = True
        if per_request_cost_constraints and not per_request_cost_is_binding:
            learning.append("per-request dollar runway needs a measured request-cost growth series")
        if config.session_block_cost_usd > 0 and cost_series is not None:
            cutoff = as_of - timedelta(seconds=config.session_window_seconds)
            window_cost = sum(
                turn_cost for turn, turn_cost in zip(turns, cost_series) if turn.at >= cutoff
            )
            add(
                config.session_block_cost_usd,
                window_cost,
                usd_burn,
                BindingConstraint.BUDGET,
                23,
            )

    # A known hard stop is terminal.  Do not let a lower-precedence missing
    # or failed rolling-cap measurement downgrade it to unknown/error.
    known_hard = sorted(
        (candidate for candidate in candidates if candidate.hard_stop and candidate.turns == 0),
        key=lambda candidate: candidate.order,
    )
    if known_hard:
        winner = known_hard[0]
        return Runway(
            status=RunwayStatus.AVAILABLE,
            turns=0,
            binding_constraint=winner.binding,
            guard_state=GuardState.HARD_STOP,
            reason="an active hard-stop constraint is already binding",
        )

    agent_attributions = [_text(turn.row.agent_id) for turn in turns]
    attributed_agents = set(agent_attributions)
    has_missing_attribution = "" in attributed_agents
    attributed_agents.discard("")
    agent_id = (
        next(iter(attributed_agents))
        if len(attributed_agents) == 1 and not has_missing_attribution
        else ""
    )
    if config.rolling_caps_enabled:
        per_agent_rolling = any(
            value > 0
            for value in (
                config.rolling_caps_per_agent_max_cost_usd,
                config.rolling_caps_per_agent_max_tokens_total,
                config.rolling_caps_per_agent_max_cache_read_tokens,
            )
        )
        fleet_rolling = any(
            value > 0
            for value in (
                config.rolling_caps_per_fleet_max_cost_usd,
                config.rolling_caps_per_fleet_max_tokens_total,
                config.rolling_caps_per_fleet_max_cache_read_tokens,
            )
        )
        if per_agent_rolling and not agent_id:
            unavailable.append("active per-agent rolling cap lacks one stable agent attribution")
        needs_usage = fleet_rolling or (per_agent_rolling and bool(agent_id))
        resolved_usage = rolling_usage
        if needs_usage and rolling_usage is _AUTO_ROLLING_USAGE:
            from tokenpak.proxy.spend_guard.rolling_caps import compute_rolling_usage

            try:
                resolved_usage = compute_rolling_usage(
                    agent_id,
                    config.rolling_caps_window_seconds,
                    monitor_db_path=monitor_db_path,
                )
            except Exception:
                logger.exception("session-economics rolling-cap usage resolution failed")
                return Runway(
                    status=RunwayStatus.ERROR,
                    turns=None,
                    binding_constraint=BindingConstraint.ROLLING_CAP,
                    guard_state=GuardState.SOFT_BLOCK,
                    reason="rolling-cap usage failed; inspect local proxy logs before retrying",
                )
        if needs_usage and resolved_usage is None:
            return Runway(
                status=RunwayStatus.ERROR,
                turns=None,
                binding_constraint=BindingConstraint.ROLLING_CAP,
                guard_state=GuardState.SOFT_BLOCK,
                reason="rolling-cap usage is unmeasurable",
            )
        if needs_usage:
            if not isinstance(resolved_usage, Mapping):
                return Runway(
                    status=RunwayStatus.ERROR,
                    turns=None,
                    binding_constraint=BindingConstraint.ROLLING_CAP,
                    guard_state=GuardState.SOFT_BLOCK,
                    reason="rolling-cap usage returned an invalid shape",
                )
            rolling_specs = (
                ("agent_cost_usd", config.rolling_caps_per_agent_max_cost_usd, usd_burn),
                ("agent_tokens_total", config.rolling_caps_per_agent_max_tokens_total, token_burn),
                (
                    "agent_cache_read_tokens",
                    config.rolling_caps_per_agent_max_cache_read_tokens,
                    _ewma(cache_burns) if cache_burns else None,
                ),
                ("fleet_cost_usd", config.rolling_caps_per_fleet_max_cost_usd, usd_burn),
                ("fleet_tokens_total", config.rolling_caps_per_fleet_max_tokens_total, token_burn),
                (
                    "fleet_cache_read_tokens",
                    config.rolling_caps_per_fleet_max_cache_read_tokens,
                    _ewma(cache_burns) if cache_burns else None,
                ),
            )
            for index, (key, cap, burn) in enumerate(rolling_specs):
                if cap <= 0:
                    continue
                if key.startswith("agent_") and not agent_id:
                    continue
                try:
                    used = _rolling_value(resolved_usage, key)
                except _SessionForecastDataError as exc:
                    return Runway(
                        status=RunwayStatus.ERROR,
                        turns=None,
                        binding_constraint=BindingConstraint.ROLLING_CAP,
                        guard_state=GuardState.SOFT_BLOCK,
                        reason=str(exc),
                    )
                if used >= cap:
                    candidates.append(
                        _RunwayCandidate(
                            0,
                            BindingConstraint.ROLLING_CAP,
                            30 + index,
                        )
                    )
                    continue
                if burn is None:
                    unavailable.append(f"active {key} cap lacks a measured per-turn burn")
                    continue
                add(float(cap), used, burn, BindingConstraint.ROLLING_CAP, 30 + index)

    if unavailable:
        return Runway(
            status=RunwayStatus.UNAVAILABLE,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason="; ".join(dict.fromkeys(unavailable)),
        )
    known_soft = sorted(
        (candidate for candidate in candidates if candidate.turns == 0),
        key=lambda candidate: candidate.order,
    )
    if known_soft:
        winner = known_soft[0]
        return Runway(
            status=RunwayStatus.AVAILABLE,
            turns=0,
            binding_constraint=winner.binding,
            guard_state=GuardState.SOFT_BLOCK,
            reason="an active soft-block constraint is already binding",
        )
    if learning:
        return Runway(
            status=RunwayStatus.LEARNING,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason="; ".join(dict.fromkeys(learning)),
        )
    if not candidates:
        return Runway(
            status=RunwayStatus.UNAVAILABLE,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason="no active constraint has a finite measurable runway",
        )

    winner = min(candidates, key=lambda candidate: (candidate.turns, candidate.order))
    current_soft = any(candidate.turns == 0 for candidate in candidates)
    amber = False
    if token_current is not None and config.warn_tokens > 0:
        amber = token_current >= config.warn_tokens
    if latest_cost is not None and config.warn_cost_usd > 0:
        amber = amber or latest_cost >= config.warn_cost_usd
    return Runway(
        status=RunwayStatus.AVAILABLE,
        turns=winner.turns,
        binding_constraint=winner.binding,
        guard_state=(
            GuardState.SOFT_BLOCK
            if current_soft
            else GuardState.AMBER
            if amber
            else GuardState.ALLOW
        ),
    )


def _empty_forecast(status: ForecastStatus, reason: str) -> Forecast:
    if status is ForecastStatus.ERROR:
        state = ValueState.ERROR
    elif status is ForecastStatus.LEARNING:
        state = ValueState.NO_DATA
    else:
        state = ValueState.UNAVAILABLE

    def interval(unit: str) -> IntervalEstimate:
        return IntervalEstimate(state=state, reason=reason, unit=unit)

    def numeric(unit: str) -> NumericValue:
        return NumericValue(state=state, reason=reason, unit=unit)

    return Forecast(
        status=status,
        remaining_tokens_likely_50=interval("tokens"),
        remaining_tokens_ceiling_90=numeric("tokens"),
        remaining_cost_usd_likely_50=interval("usd"),
        remaining_cost_usd_ceiling_90=numeric("usd"),
        expected_turns=interval("turns"),
        coverage=Coverage(drift_state=DriftState.UNKNOWN),
        predicted_block_probability=numeric("probability"),
        reason=reason,
    )


def _empty_payload(
    *,
    as_of: datetime,
    session_id: str,
    model_hint: str,
    state: str,
    reason: str,
) -> SessionEconomics:
    if state == "error":
        value_state = ValueState.ERROR
        runway_status = RunwayStatus.ERROR
        forecast_status = ForecastStatus.ERROR
    else:
        value_state = ValueState.NO_DATA
        runway_status = RunwayStatus.UNAVAILABLE
        forecast_status = ForecastStatus.UNAVAILABLE

    def numeric(unit: str) -> NumericValue:
        return NumericValue(state=value_state, reason=reason, unit=unit)

    identity = ValueState.OBSERVED if session_id else value_state
    return SessionEconomics(
        as_of=as_of.isoformat(),
        session=SessionRef(
            id=session_id or None,
            identity_state=identity,
            turns_observed=0,
            model=ModelRef(id=_text(model_hint) or "unknown", effort="unknown"),
            reason="" if session_id else reason,
        ),
        facts=SessionFacts(
            input_tokens=numeric("tokens"),
            output_tokens=numeric("tokens"),
            cache_read_tokens=numeric("tokens"),
            cache_write_tokens=numeric("tokens"),
            cost_usd=CostValue(state=value_state, basis=CostBasis.UNKNOWN, reason=reason),
        ),
        state=SessionState(
            context_tokens=numeric("tokens"),
            base_tokens=numeric("tokens"),
            context_growth_ewma=numeric("tokens/turn"),
            burn_tokens_per_turn=numeric("tokens/turn"),
            burn_usd_per_turn=numeric("usd/turn"),
            burn_slope=BurnSlope.UNKNOWN,
            idle_seconds=numeric("seconds"),
            cache_ttl_seconds=numeric("seconds"),
            cache_state=CacheState.UNKNOWN,
        ),
        runway=Runway(
            status=runway_status,
            turns=None,
            binding_constraint=BindingConstraint.UNKNOWN,
            guard_state=GuardState.UNKNOWN,
            reason=reason,
        ),
        forecast=_empty_forecast(forecast_status, reason),
        advisory=None,
    )


def _build_session_economics(
    session_id: str,
    *,
    monitor_db_path: str | None = None,
    model_hint: str = "",
    now: datetime | None = None,
    spend_guard_config: SpendGuardConfig | None = None,
    rate_provenance: RateProvenance | None = None,
    rolling_usage: object = _AUTO_ROLLING_USAGE,
    ledger_read: _SessionLedgerRead | None = None,
) -> SessionEconomics:
    """Build the versioned OSS session-economics contract.

    Optional dependency injections exist for deterministic offline replay;
    the live endpoint supplies only the session identity and monitor path.
    """
    as_of = _now_utc(now)
    stable_id = session_id.strip() if isinstance(session_id, str) else ""
    if not stable_id:
        return _empty_payload(
            as_of=as_of,
            session_id="",
            model_hint=model_hint,
            state="no_data",
            reason="stable session identity is missing",
        )

    read = ledger_read or _read_completed_session_rows(
        stable_id,
        monitor_db_path=monitor_db_path,
    )
    if read.state != "observed":
        return _empty_payload(
            as_of=as_of,
            session_id=stable_id,
            model_hint=model_hint,
            state=read.state,
            reason=read.reason or "session ledger is unavailable",
        )

    try:
        turns = _deduplicate_rows(read.rows)
        if not turns:
            return _empty_payload(
                as_of=as_of,
                session_id=stable_id,
                model_hint=model_hint,
                state="no_data",
                reason="session has no deduplicated completed turns",
            )
        cost, cost_series = _cost_value(
            turns,
            as_of=as_of,
            rate_provenance=rate_provenance,
        )
        state, contexts, token_burns, cache_burns = _state_values(
            turns,
            as_of=as_of,
            cost=cost,
            cost_series=cost_series,
        )
        config = spend_guard_config or load_config()
        runway = _resolve_runway(
            turns,
            state,
            cost,
            cost_series,
            contexts,
            token_burns,
            cache_burns,
            as_of=as_of,
            config=config,
            monitor_db_path=monitor_db_path,
            rolling_usage=rolling_usage,
        )
    except (_SessionForecastDataError, ValueError) as exc:
        return _empty_payload(
            as_of=as_of,
            session_id=stable_id,
            model_hint=model_hint,
            state="error",
            reason=str(exc),
        )

    latest = turns[-1].row
    model = _text(latest.model) or _text(model_hint) or "unknown"
    effort = _text(latest.reasoning_effort) or "unknown"
    facts = SessionFacts(
        input_tokens=_fact_total(turns, "provider_input_tokens"),
        output_tokens=_fact_total(turns, "provider_output_tokens"),
        cache_read_tokens=_fact_total(turns, "provider_cache_read_tokens"),
        cache_write_tokens=_fact_total(turns, "provider_cache_creation_tokens"),
        cost_usd=cost,
    )
    forecast_status = ForecastStatus.LEARNING if len(turns) >= 1 else ForecastStatus.UNAVAILABLE
    forecast_reason = "remaining-task forecast is not implemented"
    return SessionEconomics(
        as_of=as_of.isoformat(),
        session=SessionRef(
            id=stable_id,
            identity_state=ValueState.OBSERVED,
            turns_observed=len(turns),
            model=ModelRef(id=model, effort=effort),
        ),
        facts=facts,
        state=state,
        runway=runway,
        forecast=_empty_forecast(forecast_status, forecast_reason),
        advisory=None,
    )


__all__: list[str] = []
