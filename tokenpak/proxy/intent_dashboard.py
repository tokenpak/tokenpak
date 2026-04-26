# SPDX-License-Identifier: Apache-2.0
"""Intent Layer Phase 1.1 — dashboard-shaped read-model.

A thin UI-friendly view over the Phase 1 :func:`build_report`
output. The wire shape produced by :func:`collect_dashboard` is
the **stable API contract** consumed by:

  - the proxy's ``GET /api/intent/report?window=Nd`` endpoint
  - the dashboard UI's intent panel (``tokenpak/dashboard/``)
  - any third-party tool that wants to read TokenPak's intent
    aggregations programmatically

Three sections in the returned dict:

  - ``cards`` — the nine numeric / list cards the dashboard panel
    renders (one card = one box on screen).
  - ``operator_panel`` — the five narrative items the Phase 1
    report surfaces under "Recommended review areas" + the top-N
    slot/reason lists.
  - ``metadata`` — window identity, telemetry-store path, schema
    version stamp.

**This module is read-only.** Reuses Phase 1's query layer
verbatim — every privacy / window / never-raises invariant flows
through unchanged. Percentages are pre-computed here so the UI
doesn't have to do float math; the underlying counts remain
available for clients that prefer to compute their own ratios.

No raw prompt content. The Phase 1 query layer reads
``raw_prompt_hash`` at most; this read-model does not surface even
that. Asserted in
``tests/test_intent_dashboard.py::TestPrivacyContract``.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, Optional

# Bumped when the wire shape changes in a backward-incompatible
# way. Consumers can pin against this version.
DASHBOARD_SCHEMA_VERSION: str = "intent-dashboard-v1"


def _pct(numer: int, denom: int) -> float:
    """Percentage (0.0–100.0), rounded to one decimal place."""
    if denom <= 0:
        return 0.0
    return round(100.0 * numer / denom, 1)


def collect_dashboard(
    *,
    window_days: Optional[int] = 14,
    db_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Run :func:`build_report` and reshape it for dashboard consumers.

    The returned dict is the **API contract**. Every key documented
    in :mod:`tokenpak.proxy.intent_dashboard` is part of the
    stable surface; new keys may be added in v1 without bumping
    :data:`DASHBOARD_SCHEMA_VERSION`, but existing keys MUST NOT
    change shape or semantics.
    """
    from tokenpak.proxy.intent_report import build_report

    report = build_report(
        window_days=window_days,
        db_path=db_path,
        now=now,
        top_n=top_n,
    )

    total = report.total_classified

    # ── Cards (nine boxes) ─────────────────────────────────────────
    intent_class_dist = []
    for cls, count in report.intent_class_distribution.items():
        intent_class_dist.append({
            "intent_class": cls,
            "count": count,
            "pct": _pct(count, total),
            "avg_confidence": report.avg_confidence_by_class.get(cls, 0.0),
        })

    catch_all_dist = []
    for reason, count in report.catch_all_reason_distribution.items():
        catch_all_dist.append({
            "catch_all_reason": reason,
            "count": count,
            "pct": _pct(count, total),
        })

    top_missing_slots = [
        {"slot": name, "count": count, "pct": _pct(count, total)}
        for name, count in report.top_missing_slots
    ]

    cards: Dict[str, Any] = {
        "total_classified": {
            "value": total,
        },
        "intent_class_distribution": {
            "items": intent_class_dist,
        },
        "average_confidence": {
            # Single weighted average across all classifications.
            # Weighted by per-class count so the value reflects the
            # mix of traffic, not an unweighted mean of class
            # averages.
            "value": _weighted_avg_confidence(report),
        },
        "low_confidence_count": {
            "value": report.low_confidence_count,
            "pct_of_total": _pct(report.low_confidence_count, total),
            "threshold": report.low_confidence_threshold,
        },
        "catch_all_reason_distribution": {
            "items": catch_all_dist,
        },
        "top_missing_slots": {
            "items": top_missing_slots,
        },
        "tip_headers_emitted_vs_telemetry_only": {
            "tip_headers_emitted": report.tip_headers_emitted,
            "telemetry_only": report.telemetry_only,
            "tip_headers_stripped": report.tip_headers_stripped,
            "emitted_pct": _pct(report.tip_headers_emitted, total),
            "telemetry_only_pct": _pct(report.telemetry_only, total),
        },
        "adapters_eligible": {
            # Adapters that DECLARE tip.intent.contract-headers-v1.
            "items": list(report.adapters_eligible),
            "count": len(report.adapters_eligible),
        },
        "adapters_blocking": {
            # Adapters that do NOT declare the gate label — the
            # Phase 0 default.
            "items": list(report.adapters_blocking),
            "count": len(report.adapters_blocking),
        },
    }

    # ── Operator panel (five narrative items) ──────────────────────
    operator_panel: Dict[str, Any] = {
        "most_common_missing_slots": top_missing_slots,
        "most_common_catch_all_reasons": catch_all_dist[:top_n],
        "adapters_eligible_for_tip_headers": list(report.adapters_eligible),
        "adapters_requiring_capability_declaration": list(report.adapters_blocking),
        "recommended_review_areas": list(report.review_areas),
    }

    # ── Metadata ───────────────────────────────────────────────────
    metadata: Dict[str, Any] = {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "window_days": report.window_days,
        "window_cutoff_iso": report.window_cutoff_iso,
        "telemetry_store_path": report.db_path,
        "low_confidence_threshold": report.low_confidence_threshold,
        "phase": "intent-layer-phase-1.1",
        "observation_only": True,
    }

    return {
        "metadata": metadata,
        "cards": cards,
        "operator_panel": operator_panel,
    }


def _weighted_avg_confidence(report) -> float:
    """Volume-weighted mean confidence across all classifications.

    Falls back to ``0.0`` on zero-volume windows so the dashboard
    card has a sane numeric value to render (rather than ``null``).
    """
    total = report.total_classified
    if total <= 0:
        return 0.0
    weighted = 0.0
    for cls, count in report.intent_class_distribution.items():
        weighted += count * report.avg_confidence_by_class.get(cls, 0.0)
    return round(weighted / total, 4)


# ---------------------------------------------------------------------------
# Window parsing — re-export from intent_report so the API endpoint
# has one import target.
# ---------------------------------------------------------------------------


def parse_window_or_default(spec: Optional[str]) -> Optional[int]:
    """Validate ``spec`` (an ``Nd`` form). Default to 14 days when
    missing. Raises :class:`ValueError` on bad input so the API
    caller surfaces a clean 400.

    Distinct from :func:`tokenpak.proxy.intent_report.parse_window`
    — that one returns ``None`` for empty/missing (= "all rows").
    Dashboard semantics require the **default to be 14d** so a
    fresh API call without ``?window=`` doesn't accidentally
    surface a multi-year history.
    """
    from tokenpak.proxy.intent_report import parse_window

    if spec is None or spec == "":
        return 14
    return parse_window(spec)


__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "collect_dashboard",
    "parse_window_or_default",
]
