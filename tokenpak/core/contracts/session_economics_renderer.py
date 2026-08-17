# SPDX-License-Identifier: Apache-2.0
"""Presentation-neutral projection over a validated session-economics payload.

One pure module renders the deterministic trip-computer state for every
surface: the status line, the status block, the dashboard, and the companion
tool. Surfaces stay thin adapters — they fetch a payload, validate it with
``SessionEconomics.from_dict()``, and call these functions. Nothing here
recalculates economics, touches a clock, reads configuration, or performs
I/O; the canonical machine-readable form remains ``SessionEconomics.to_dict``
/ ``to_json`` and is deliberately not re-implemented in this module.

Rendering is truth-preserving: observed facts print as plain values,
estimates are marked ``~``, and empty/unavailable/error states print as
words, never as zeros. A forecast that is still learning (or not available)
says so explicitly.
"""

from __future__ import annotations

from tokenpak.core.contracts.session_economics import (
    BurnSlope,
    CostBasis,
    CostValue,
    ForecastStatus,
    IntervalEstimate,
    NumericValue,
    RunwayStatus,
    SessionEconomics,
    ValueState,
)

_SLOPE_MARK = {
    BurnSlope.UP: "↑",
    BurnSlope.DOWN: "↓",
    BurnSlope.FLAT: "→",
    BurnSlope.UNKNOWN: "?",
}

_STATE_WORD = {
    ValueState.NO_DATA: "no data",
    ValueState.UNAVAILABLE: "unavailable",
    ValueState.ERROR: "error",
}


def _fmt_count(value: float) -> str:
    """Deterministic compact token/turn count (no locale, no clock)."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.0f}k"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _numeric(value: NumericValue, *, suffix: str = "") -> str:
    """Render a NumericValue without ever faking a number."""
    if value.state is ValueState.OBSERVED:
        assert value.value is not None
        return f"{_fmt_count(float(value.value))}{suffix}"
    if value.state is ValueState.ESTIMATED:
        assert value.value is not None
        return f"~{_fmt_count(float(value.value))}{suffix}"
    return _STATE_WORD[value.state]


def _cost(value: CostValue) -> str:
    if value.state is ValueState.OBSERVED:
        assert value.value is not None
        return f"${float(value.value):.2f}"
    if value.state is ValueState.ESTIMATED:
        assert value.value is not None
        return f"~${float(value.value):.2f} est"
    if value.basis is CostBasis.SUBSCRIPTION:
        return "subscription"
    return _STATE_WORD[value.state]


def _interval(value: IntervalEstimate, *, suffix: str = "") -> str:
    if value.state is ValueState.ESTIMATED:
        assert value.low is not None and value.high is not None
        return f"~{_fmt_count(float(value.low))}–{_fmt_count(float(value.high))}{suffix}"
    return _STATE_WORD[value.state]


def _reason(text: str) -> str:
    return f" ({text})" if text else ""


def render_line(economics: SessionEconomics) -> str:
    """One-line trip-computer summary for the default status surface."""
    session = economics.session
    if session.identity_state is not ValueState.OBSERVED:
        word = _STATE_WORD.get(session.identity_state, session.identity_state.value)
        return f"session economics: {word}{_reason(session.reason)}"

    facts = economics.facts
    state = economics.state
    runway = economics.runway
    parts = [
        f"in {_numeric(facts.input_tokens)}",
        f"out {_numeric(facts.output_tokens)}",
        f"cost {_cost(facts.cost_usd)}",
        (f"burn {_numeric(state.burn_tokens_per_turn)}/turn {_SLOPE_MARK[state.burn_slope]}"),
    ]
    if runway.status is RunwayStatus.AVAILABLE:
        parts.append(f"runway {runway.turns} turns ({runway.binding_constraint.value})")
    else:
        parts.append(f"runway {runway.status.value}")
    parts.append(f"guard {runway.guard_state.value}")
    forecast = economics.forecast
    if (
        forecast.status is ForecastStatus.AVAILABLE
        and forecast.remaining_tokens_likely_50.state is ValueState.ESTIMATED
    ):
        parts.append(
            f"left {_interval(forecast.remaining_tokens_likely_50)} "
            f"(90% ≤ {_numeric(forecast.remaining_tokens_ceiling_90)})"
        )
    else:
        parts.append(f"forecast {forecast.status.value}")
    return "session economics: " + " · ".join(parts)


def render_block(economics: SessionEconomics) -> str:
    """Multi-line trip-computer block for the full status surface.

    Facts, estimates (~), and unknown states are visually distinct; every
    non-value state prints its word and, when present, its reason.
    """
    session = economics.session
    lines: list[str] = ["Session economics"]

    if session.identity_state is not ValueState.OBSERVED:
        word = _STATE_WORD.get(session.identity_state, session.identity_state.value)
        lines.append(f"  session        {word}{_reason(session.reason)}")
        lines.append(
            f"  forecast       {economics.forecast.status.value}"
            f"{_reason(economics.forecast.reason)}"
        )
        lines.append("  legend         plain=observed  ~=estimate  words=no value")
        return "\n".join(lines)

    facts = economics.facts
    state = economics.state
    runway = economics.runway
    forecast = economics.forecast

    lines.append(
        f"  session        {session.id} · {session.model.id}"
        f" ({session.model.effort}) · {session.turns_observed} turns"
    )
    lines.append(
        "  spent          "
        f"in {_numeric(facts.input_tokens)} · out {_numeric(facts.output_tokens)} · "
        f"cache r/w {_numeric(facts.cache_read_tokens)}/{_numeric(facts.cache_write_tokens)}"
    )
    lines.append(f"  cost           {_cost(facts.cost_usd)}{_reason(facts.cost_usd.reason)}")
    lines.append(
        "  context        "
        f"{_numeric(state.context_tokens)} tokens · base {_numeric(state.base_tokens)} · "
        f"growth {_numeric(state.context_growth_ewma)}/turn"
    )
    lines.append(
        "  burn           "
        f"{_numeric(state.burn_tokens_per_turn)} tokens/turn "
        f"{_SLOPE_MARK[state.burn_slope]} · {_cost_per_turn(state.burn_usd_per_turn)}"
    )
    lines.append(
        "  cache/idle     "
        f"state {state.cache_state.value} · ttl {_numeric(state.cache_ttl_seconds)}s · "
        f"idle {_numeric(state.idle_seconds)}s"
    )
    if runway.status is RunwayStatus.AVAILABLE:
        lines.append(
            "  runway         "
            f"{runway.turns} turns · binding {runway.binding_constraint.value} · "
            f"guard {runway.guard_state.value}"
        )
    else:
        lines.append(
            "  runway         "
            f"{runway.status.value}{_reason(runway.reason)} · guard {runway.guard_state.value}"
        )
    if forecast.status is ForecastStatus.AVAILABLE:
        lines.append(
            "  forecast       "
            f"remaining {_interval(forecast.remaining_tokens_likely_50)} tokens "
            f"(90% ceiling {_numeric(forecast.remaining_tokens_ceiling_90)}) · "
            f"turns {_interval(forecast.expected_turns)}"
        )
        if forecast.remaining_cost_usd_likely_50.state is ValueState.ESTIMATED:
            lines.append(
                "  forecast cost  "
                f"~${float(forecast.remaining_cost_usd_likely_50.low):.2f}–"
                f"${float(forecast.remaining_cost_usd_likely_50.high):.2f} "
                f"(90% ≤ ${float(forecast.remaining_cost_usd_ceiling_90.value):.2f})"
            )
        coverage = forecast.coverage
        observed = (
            f"{coverage.observed * 100.0:.0f}%" if coverage.observed is not None else "unmeasured"
        )
        block_prob = forecast.predicted_block_probability
        block_text = (
            f" · block risk ~{float(block_prob.value) * 100.0:.0f}%"
            if block_prob.state is ValueState.ESTIMATED and block_prob.value is not None
            else ""
        )
        lines.append(
            "  calibration    "
            f"measured coverage {observed} · history {coverage.history_n} sessions · "
            f"{coverage.drift_state.value}{block_text}"
        )
    else:
        lines.append(f"  forecast       {forecast.status.value}{_reason(forecast.reason)}")
    lines.append("  legend         plain=observed  ~=estimate  words=no value")
    return "\n".join(lines)


def _cost_per_turn(value: NumericValue) -> str:
    if value.state is ValueState.OBSERVED:
        assert value.value is not None
        return f"${float(value.value):.4f}/turn"
    if value.state is ValueState.ESTIMATED:
        assert value.value is not None
        return f"~${float(value.value):.4f}/turn est"
    return f"usd/turn {_STATE_WORD[value.state]}"


__all__ = ["render_block", "render_line"]
