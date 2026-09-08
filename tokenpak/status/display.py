# SPDX-License-Identifier: Apache-2.0
"""Width-bounded ASCII adapter for the shared economics contract."""

from __future__ import annotations

from tokenpak.core.contracts.session_economics import ForecastStatus, RunwayStatus, ValueState
from tokenpak.core.contracts.session_economics_renderer import _cost, _interval, _numeric
from tokenpak.status.snapshot import StatusSnapshot

WIDTHS = (32, 48, 64, 80, 100, 120, 160, 240)


def render(snapshot: StatusSnapshot, columns: int = 80) -> str:
    """Keep whole fields, estimate labels and guard state; never clip a number."""
    identity = snapshot.session_id[:8]
    prefix = f"TP {identity}" if identity else "TokenPak"
    data = snapshot.economics
    if data is None:
        parts = [prefix, snapshot.reason]
    else:
        forecast = data.forecast
        parts = [prefix, f"guard {data.runway.guard_state.value}"]
        if forecast.status is ForecastStatus.AVAILABLE:
            band = forecast.remaining_cost_usd_likely_50
            ceiling = forecast.remaining_cost_usd_ceiling_90
            unit = "USD"
            if band.state is ValueState.ESTIMATED:
                likely = f"{band.low:.2f}-{band.high:.2f}"
                upper = (
                    f"~{ceiling.value:.2f} est"
                    if ceiling.value is not None
                    else ceiling.state.value
                )
            else:
                band = forecast.remaining_tokens_likely_50
                ceiling = forecast.remaining_tokens_ceiling_90
                unit = "tokens"
                likely = _interval(band, label_estimate=False)
                upper = _numeric(ceiling)
            parts.append(f"remain est {likely} {unit} (50%)")
            parts.append(f"90% ceiling {upper} {unit}")
        else:
            word = "no data" if data.session.turns_observed == 0 else forecast.status.value
            parts.append(f"forecast {word}")
        parts.append(f"spent {_cost(data.facts.cost_usd)}")
        runway = data.runway
        if runway.status is RunwayStatus.AVAILABLE:
            parts.append(f"guard limit ~{runway.turns} turns est")
        else:
            parts.append(f"runway {runway.status.value}")
    # All external strings are excluded except a validated session identifier.
    # Transliterate shared formatting punctuation, making bytes == terminal columns.
    parts = [p.replace("–", "-").replace("≤", "<=") for p in parts]
    parts = ["".join(c for c in p if 32 <= ord(c) < 127 and c != "|") for p in parts]
    out = parts[0]
    for part in parts[1:]:
        candidate = out + " | " + part
        if len(candidate) <= columns:
            out = candidate
    if data is not None and "forecast" not in out and "remain" not in out:
        short = f"{prefix} | {data.forecast.status.value} | guard {data.runway.guard_state.value}"
        out = short if len(short) <= columns else f"TP | guard {data.runway.guard_state.value}"
    return out if len(out) <= columns else ("TokenPak" if columns >= 8 else "")
