# SPDX-License-Identifier: Apache-2.0
"""Phase 2.2 — dashboard-shaped read-model for the policy preview.

A thin UI-friendly view over :func:`build_policy_report`. The wire
shape produced by :func:`collect_policy_dashboard` is the **stable
API contract** consumed by:

  - ``GET /api/intent/policy-report?window=Nd``
  - The dashboard's "Intent Policy" panel
    (``tokenpak/dashboard/intent_policy.js``)
  - Any third-party tool that wants the policy aggregations
    programmatically

Three sections in the returned dict:

  - ``cards`` — eight numeric / list cards the dashboard panel
    renders. **Each card is dry-run / preview only** — the
    metadata block carries this label explicitly.
  - ``operator_panel`` — narrative slices (top recommended
    actions, top safety flags, etc.).
  - ``metadata`` — schema version, window identity, store path,
    plus the explicit ``dry_run_preview_only=true`` flag that
    consumers should display alongside the data.

Read-only. Reuses :func:`build_policy_report` so all privacy /
window / never-raises invariants flow through unchanged.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DASHBOARD_SCHEMA_VERSION: str = "intent-policy-dashboard-v1"


def _pct(numer: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return round(100.0 * numer / denom, 1)


def _top_pairs(d: Dict[str, int], top_n: int = 5) -> List[Tuple[str, int]]:
    return sorted(d.items(), key=lambda kv: -kv[1])[:top_n]


def collect_policy_dashboard(
    *,
    window_days: Optional[int] = 14,
    db_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Run :func:`build_policy_report` and reshape for dashboard consumers."""
    from tokenpak.proxy.intent_policy_report import build_policy_report

    report = build_policy_report(
        window_days=window_days, db_path=db_path, now=now,
    )
    total = report.total_decisions

    action_items = [
        {"action": a, "count": c, "pct": _pct(c, total)}
        for a, c in report.action_distribution.items()
    ]
    safety_items = [
        {"safety_flag": f, "count": c, "pct": _pct(c, total)}
        for f, c in sorted(
            report.safety_flag_distribution.items(), key=lambda kv: -kv[1]
        )
    ]
    compression_items = [
        {"compression_profile": p, "count": c}
        for p, c in report.compression_profile_distribution.items()
    ]
    cache_items = [
        {"cache_strategy": s, "count": c}
        for s, c in report.cache_strategy_distribution.items()
    ]
    delivery_items = [
        {"delivery_strategy": s, "count": c}
        for s, c in report.delivery_strategy_distribution.items()
    ]
    blocked_reason_items = [
        {"decision_reason": r, "count": c}
        for r, c in sorted(
            report.decision_reason_distribution.items(), key=lambda kv: -kv[1]
        )
        if r.endswith("_blocked_routing") or r == "unverified_provider_blocked"
    ]

    cards: Dict[str, Any] = {
        # Card 1: total
        "total_dry_run_decisions": {"value": total},
        # Card 2: top recommended actions
        "top_recommended_actions": {"items": action_items[:top_n]},
        # Card 3: top safety flags
        "top_safety_flags": {"items": safety_items[:top_n]},
        # Card 4: budget risk flags (reserved in 2.1 — counted here)
        "budget_risk_flags": {
            "value": report.budget_risk_flags,
            "pct_of_total": _pct(report.budget_risk_flags, total),
        },
        # Card 5: suggested compression profiles
        "suggested_compression_profiles": {"items": compression_items},
        # Card 6: suggested cache policies
        "suggested_cache_policies": {"items": cache_items},
        # Card 7: suggested delivery policies
        "suggested_delivery_policies": {"items": delivery_items},
        # Card 8: auto-routing blocked reasons
        "auto_routing_blocked_reasons": {"items": blocked_reason_items},
    }

    operator_panel: Dict[str, Any] = {
        "top_recommended_actions": [list(t) for t in _top_pairs(report.action_distribution, top_n)],
        "top_safety_flags": [list(t) for t in _top_pairs(report.safety_flag_distribution, top_n)],
        "top_blocked_reasons": [list(t) for t in _top_pairs(
            {
                k: v for k, v in report.decision_reason_distribution.items()
                if k.endswith("_blocked_routing") or k == "unverified_provider_blocked"
            },
            top_n,
        )],
        "recommended_review_areas": list(report.review_areas),
    }

    # Phase 2.4.2 — surface the advisory-suggestions slice under
    # a dedicated top-level key. Reuses build_suggestion_report
    # (Phase 2.4.2) so privacy / window invariants flow through.
    suggestions_section: Dict[str, Any] = {}
    try:
        from tokenpak.proxy.intent_suggestion_report import build_suggestion_report

        sugg = build_suggestion_report(
            window_days=window_days, db_path=db_path, now=now,
        )
        suggestions_section = {
            "advisory_label": (
                "Suggestions are advisory only. TokenPak has not "
                "changed routing."
            ),
            "noop_default_off": True,
            "total": sugg.total_suggestions,
            "type_distribution": [
                {"suggestion_type": t, "count": c}
                for t, c in sugg.suggestion_type_distribution.items()
            ],
            "safety_flag_distribution": [
                {"safety_flag": f, "count": c}
                for f, c in sorted(
                    sugg.safety_flag_distribution.items(),
                    key=lambda kv: -kv[1],
                )
            ],
            "recommended_action_distribution": [
                {"recommended_action": a, "count": c}
                for a, c in sugg.recommended_action_distribution.items()
            ],
            "user_visible_true_count": sugg.user_visible_true_count,
            "user_visible_false_count": sugg.user_visible_false_count,
            "expired_count": sugg.expired_count,
            "latest": list(sugg.latest_suggestions),
        }
    except Exception:  # noqa: BLE001
        # Read-only path; never raise. Pre-2.4.1 hosts will see an
        # empty section with the dry-run / advisory labelling.
        suggestions_section = {
            "advisory_label": (
                "Suggestions are advisory only. TokenPak has not "
                "changed routing."
            ),
            "noop_default_off": True,
            "total": 0,
        }

    # ``phase`` stays at intent-layer-phase-2.2 because the cards /
    # operator_panel contract is unchanged from 2.2. Phase 2.4.2's
    # additive ``suggestions`` section is identified by its own
    # noop_default_off flag inside the suggestions block above.
    metadata: Dict[str, Any] = {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "phase": "intent-layer-phase-2.2",
        "dry_run_preview_only": True,
        "preview_label": "DRY-RUN / PREVIEW ONLY — no routing decisions made",
        "suggestions_label": (
            "Suggestions are advisory only. TokenPak has not "
            "changed routing."
        ),
        "window_days": report.window_days,
        "window_cutoff_iso": report.window_cutoff_iso,
        "telemetry_store_path": report.db_path,
    }

    return {
        "metadata": metadata,
        "cards": cards,
        "operator_panel": operator_panel,
        "suggestions": suggestions_section,
    }


def parse_window_or_default(spec: Optional[str]) -> Optional[int]:
    """Validate ``spec``. Default 14 days when missing.

    Mirrors :func:`tokenpak.proxy.intent_dashboard.parse_window_or_default`
    so the two API endpoints share window semantics.
    """
    from tokenpak.proxy.intent_report import parse_window

    if spec is None or spec == "":
        return 14
    return parse_window(spec)


__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "collect_policy_dashboard",
    "parse_window_or_default",
]
