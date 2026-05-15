# SPDX-License-Identifier: Apache-2.0
"""``tokenpak features`` CLI subcommand (Beta 1, Packet G).

Lists every feature the running tokenpak install advertises along with
its entitlement state for the active license. Built dynamically from
the licensing module's ``_GATES`` table — never hardcoded — so adding a
new gated feature there immediately surfaces here without CLI edits
(``feedback_always_dynamic.md``).

Subcommands:
    (default)              List all features grouped by tier
    explain <feature>      Show the entitlement decision for one feature
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional


def build_features_parser(sub: Any) -> None:
    """Register the ``tokenpak features`` subcommand."""
    p = sub.add_parser(
        "features",
        help="List entitlement state for every gated feature",
        description=(
            "Show every feature TokenPak knows about and whether the "
            "current license entitles you to use it. Use "
            "`tokenpak features explain <feature>` for a single-feature "
            "breakdown."
        ),
    )
    fsub = p.add_subparsers(dest="features_action", required=False)

    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON instead of text",
    )
    p.add_argument(
        "--tier", default=None,
        help="Filter to a specific tier: free|pro|team|enterprise",
    )
    p.set_defaults(func=cmd_features_list)

    p_explain = fsub.add_parser(
        "explain", help="Explain entitlement for one feature"
    )
    p_explain.add_argument("feature", help="Feature key (e.g. T9_replay_system)")
    p_explain.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON",
    )
    p_explain.set_defaults(func=cmd_features_explain)


def cmd_features_list(args: Any) -> int:
    from tokenpak import licensing as _lic

    lic = _lic.load_license()
    active_tier = lic.tier if lic else _lic.TIER_FREE

    rows: list[dict] = []
    seen: set[str] = set()

    # All gated features (tracked in _GATES).
    for feature, required in sorted(_lic._GATES.items()):
        seen.add(feature)
        rows.append(_row(feature, required, active_tier, lic))

    # Free features are everything not in _GATES — there is no closed
    # enumeration of them, so we surface a single advisory row rather
    # than fabricating a list (per `feedback_always_dynamic.md`).
    rows.append({
        "feature": "(other)",
        "required_tier": _lic.TIER_FREE,
        "state": "active",
        "reason": "Free-tier features are implicit; any feature not "
                  "listed here is available without a license.",
    })

    tier_filter = (getattr(args, "tier", None) or "").strip().lower() or None
    if tier_filter:
        rows = [r for r in rows if r["required_tier"] == tier_filter]

    if getattr(args, "as_json", False):
        print(json.dumps({
            "active_tier": active_tier,
            "license_status": (lic.status if lic else "free"),
            "features": rows,
        }, indent=2, sort_keys=True))
        return 0

    print(f"Active license tier: {active_tier}")
    if lic and lic.status:
        print(f"License status     : {lic.status}")
    print("─" * 60)
    print(f"{'feature':<32} {'tier':<11} {'state':<10}")
    print(f"{'─' * 32} {'─' * 11} {'─' * 10}")
    for r in rows:
        print(f"{r['feature']:<32} {r['required_tier']:<11} {r['state']:<10}")
    print()
    print("Use `tokenpak features explain <feature>` for the per-feature reasoning.")
    return 0


def cmd_features_explain(args: Any) -> int:
    from tokenpak import licensing as _lic

    feature = args.feature.strip()
    lic = _lic.load_license()
    active_tier = lic.tier if lic else _lic.TIER_FREE

    required = _lic._GATES.get(feature)
    if required is None:
        # Unknown feature — could be a typo OR a Free feature.
        result = {
            "feature": feature,
            "required_tier": _lic.TIER_FREE,
            "state": "active",
            "reason": (
                f"{feature!r} is not in the gating table. Free-tier "
                "features are implicit; either this is a Free feature "
                "or the name is unknown to this tokenpak version."
            ),
        }
    else:
        result = _row(feature, required, active_tier, lic)

    if getattr(args, "as_json", False):
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"Feature       : {result['feature']}")
    print(f"Required tier : {result['required_tier']}")
    print(f"Active tier   : {active_tier}")
    print(f"State         : {result['state']}")
    print(f"Reason        : {result['reason']}")
    return 0


def _row(feature: str, required: str, active_tier: str, lic) -> dict:
    from tokenpak import licensing as _lic

    enabled = _lic.is_feature_enabled(feature, lic=lic)
    if enabled:
        state = "active"
        reason = f"{active_tier} ≥ {required}"
    elif lic and getattr(lic, "status", "") == "pending_validation":
        state = "locked"
        reason = (
            "License key stored but not yet validated — entitlements "
            "remain Free until validation completes."
        )
    else:
        state = "locked"
        reason = (
            f"Requires {required}; current tier is {active_tier}. "
            f"Run `tokenpak activate <key>` after purchasing."
        )
    return {
        "feature": feature,
        "required_tier": required,
        "state": state,
        "reason": reason,
    }


__all__ = ["build_features_parser", "cmd_features_list", "cmd_features_explain"]
