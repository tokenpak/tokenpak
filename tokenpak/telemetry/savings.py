# SPDX-License-Identifier: Apache-2.0
"""Savings attribution v2 — TIP-06.

Provides parsing helpers that extract per-source savings from provider API
response usage fields, and aggregation utilities for reporting.

Attribution rules (per TIP telemetry_contract.SavingsSource):
- Provider/platform cache MUST be labelled PROVIDER_PROMPT_CACHE or
  PLATFORM_CACHE; never credited to TokenPak.
- TokenPak-managed stages (semantic cache, compression, capsules, etc.)
  are labelled with the appropriate TOKENPAK_* source.
- Unknown deltas use UNATTRIBUTED.
- If model pricing is unavailable, tokens are reported but
  estimated_cost_saved is None and cost_available=False.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from tokenpak.tip.telemetry_contract import SavingsSource
from tokenpak.tip.trace_contract import SavingsAttribution


# ---------------------------------------------------------------------------
# OpenAI usage parsing
# ---------------------------------------------------------------------------


def parse_openai_usage(
    usage: Dict[str, Any],
    *,
    model: str = "",
    pricing: Optional[Dict[str, float]] = None,
) -> List[SavingsAttribution]:
    """Parse an OpenAI Responses/Chat completion usage dict into attributions.

    Extracts ``prompt_tokens_details.cached_tokens`` as PLATFORM_CACHE.
    If no provider/platform cache is detected, returns an empty list (not
    a fake unattributed record — unattributed is only emitted when a token
    delta is observed but cannot be sourced).

    Parameters
    ----------
    usage:
        The ``usage`` sub-dict from an OpenAI API response.
    model:
        Model name, used for cost lookup.
    pricing:
        Optional dict with ``input_per_token`` and ``output_per_token`` keys.
        When None, token counts are reported but cost estimates are omitted.
    """
    results: List[SavingsAttribution] = []
    if not usage:
        return results

    # prompt_tokens_details.cached_tokens → PLATFORM_CACHE
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0)

    if cached_tokens > 0:
        cost_saved: Optional[float] = None
        cost_available = False
        if pricing and "input_per_token" in pricing:
            cost_saved = round(cached_tokens * pricing["input_per_token"], 8)
            cost_available = True

        results.append(
            SavingsAttribution(
                source=SavingsSource.PLATFORM_CACHE,
                raw_tokens=int(usage.get("prompt_tokens", 0) or 0) + cached_tokens,
                sent_tokens=int(usage.get("prompt_tokens", 0) or 0),
                saved_tokens=cached_tokens,
                estimated_cost_saved=cost_saved,
                cost_available=cost_available,
                notes=f"openai cached_tokens={cached_tokens}",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Anthropic usage parsing
# ---------------------------------------------------------------------------


def parse_anthropic_usage(
    usage: Dict[str, Any],
    *,
    model: str = "",
    pricing: Optional[Dict[str, float]] = None,
) -> List[SavingsAttribution]:
    """Parse an Anthropic API response usage dict into attributions.

    Extracts ``cache_read_input_tokens`` as PROVIDER_PROMPT_CACHE.

    Parameters
    ----------
    usage:
        The ``usage`` sub-dict from an Anthropic API response.
    model:
        Model name, used for cost lookup.
    pricing:
        Optional dict with ``cache_read_per_token`` key.
        When None, token counts are reported but cost estimates are omitted.
    """
    results: List[SavingsAttribution] = []
    if not usage:
        return results

    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)

    if cache_read > 0:
        cost_saved: Optional[float] = None
        cost_available = False
        input_tokens = int(usage.get("input_tokens", 0) or 0)

        if pricing and "input_per_token" in pricing:
            # Anthropic cache_read is billed at 0.10× input rate; savings vs
            # full price = 0.90× input per cached token.
            savings_rate = pricing["input_per_token"] * 0.90
            cost_saved = round(cache_read * savings_rate, 8)
            cost_available = True

        results.append(
            SavingsAttribution(
                source=SavingsSource.PROVIDER_PROMPT_CACHE,
                raw_tokens=input_tokens + cache_read,
                sent_tokens=input_tokens,
                saved_tokens=cache_read,
                estimated_cost_saved=cost_saved,
                cost_available=cost_available,
                notes=f"anthropic cache_read_input_tokens={cache_read}",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class SourceSummary:
    """Aggregate savings metrics for one attribution source."""

    source: str
    saved_tokens: int = 0
    estimated_cost_saved: float = 0.0
    cost_available: bool = False
    request_count: int = 0
    credited_to_tokenpak: bool = False


def aggregate_attributions(
    attributions: Sequence[SavingsAttribution],
) -> Dict[str, SourceSummary]:
    """Group attributions by source and sum token/cost figures."""
    result: Dict[str, SourceSummary] = {}
    for attr in attributions:
        if attr.source not in result:
            result[attr.source] = SourceSummary(
                source=attr.source,
                credited_to_tokenpak=attr.source in SavingsSource.TOKENPAK_MANAGED,
            )
        summary = result[attr.source]
        summary.saved_tokens += attr.saved_tokens
        summary.request_count += 1
        if attr.estimated_cost_saved is not None:
            summary.estimated_cost_saved += attr.estimated_cost_saved
            summary.cost_available = True
    return result


def format_savings_by_source(
    by_source: Dict[str, SourceSummary],
    *,
    days: int = 7,
) -> str:
    """Return a human-readable savings breakdown by attribution source."""
    if not by_source:
        return "No savings attribution data for this period."

    total_tp_tokens = sum(
        s.saved_tokens for s in by_source.values() if s.credited_to_tokenpak
    )
    total_ext_tokens = sum(
        s.saved_tokens for s in by_source.values() if not s.credited_to_tokenpak
        and s.source != SavingsSource.UNATTRIBUTED
    )
    total_cost = sum(
        s.estimated_cost_saved for s in by_source.values() if s.cost_available
    )

    lines = [
        f"Savings Attribution — Last {days} Days",
        "─" * 48,
        "",
    ]

    # TokenPak-managed savings first
    tp_entries = [s for s in by_source.values() if s.credited_to_tokenpak]
    if tp_entries:
        lines.append("TokenPak-managed:")
        for s in sorted(tp_entries, key=lambda x: -x.saved_tokens):
            cost_str = f"  ${s.estimated_cost_saved:.4f}" if s.cost_available else "  (price N/A)"
            lines.append(f"  {s.source:<38} {s.saved_tokens:>8} tok{cost_str}")

    # Provider/platform savings (not credited to TokenPak)
    ext_entries = [
        s for s in by_source.values()
        if not s.credited_to_tokenpak and s.source != SavingsSource.UNATTRIBUTED
    ]
    if ext_entries:
        lines.append("")
        lines.append("Provider/Platform (not credited to TokenPak):")
        for s in sorted(ext_entries, key=lambda x: -x.saved_tokens):
            cost_str = f"  ${s.estimated_cost_saved:.4f}" if s.cost_available else "  (price N/A)"
            lines.append(f"  {s.source:<38} {s.saved_tokens:>8} tok{cost_str}")

    # Unattributed
    unattr = by_source.get(SavingsSource.UNATTRIBUTED)
    if unattr and unattr.saved_tokens > 0:
        lines.append("")
        lines.append(f"  unattributed                           {unattr.saved_tokens:>8} tok")

    lines.append("")
    if total_tp_tokens > 0 or total_ext_tokens > 0:
        lines.append(f"  TokenPak-managed total:  {total_tp_tokens:>8} tokens saved")
        lines.append(f"  Provider/platform total: {total_ext_tokens:>8} tokens (not overclaimed)")
    if total_cost > 0:
        lines.append(f"  Estimated cost saved:    ${total_cost:.4f}")
    else:
        lines.append("  Estimated cost saved:    (configure model pricing for cost estimates)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB row helpers (used by TelemetryDB)
# ---------------------------------------------------------------------------


def attribution_to_row(
    request_id: str,
    attribution: SavingsAttribution,
    *,
    timestamp: Optional[float] = None,
    platform: Optional[str] = None,
    model: str = "",
) -> Dict[str, Any]:
    """Serialize a SavingsAttribution into a tp_savings_attribution row dict."""
    return {
        "request_id": request_id,
        "timestamp": timestamp or time.time(),
        "source": attribution.source,
        "raw_tokens": attribution.raw_tokens,
        "sent_tokens": attribution.sent_tokens,
        "saved_tokens": attribution.saved_tokens,
        "estimated_cost_saved": attribution.estimated_cost_saved or 0.0,
        "cost_available": int(attribution.cost_available),
        "credited_to_tokenpak": int(attribution.credited_to_tokenpak),
        "platform": platform or "",
        "model": model,
        "notes": attribution.notes or "",
    }


__all__ = [
    "parse_openai_usage",
    "parse_anthropic_usage",
    "aggregate_attributions",
    "format_savings_by_source",
    "SourceSummary",
    "attribution_to_row",
]
