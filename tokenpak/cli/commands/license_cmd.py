# SPDX-License-Identifier: Apache-2.0
"""CLI handlers for `tokenpak license`, `tokenpak plan`, `tokenpak activate`,
`tokenpak deactivate`. Free-tier today; Pro/Team/Enterprise surface ready.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from tokenpak import licensing as _lic


def _render_summary(s: dict[str, Any]) -> str:
    lines = [""]
    lines.append(f"  TOKENPAK license — {s['tier_label']}")
    lines.append("  " + "─" * 40)
    lines.append(f"  Tier      {s['tier_label']}")
    lines.append(f"  Status    {s['status']}")
    if s.get("email"):
        lines.append(f"  Email     {s['email']}")
    if s.get("activated_at"):
        lines.append(f"  Activated {s['activated_at']}")
    if s.get("expires_at"):
        lines.append(f"  Expires   {s['expires_at']}")
    if s.get("has_key"):
        lines.append(f"  Key       stored ({s['license_path']})")
    else:
        lines.append("  Key       (none — Free tier)")
    lines.append("")
    lines.append(
        f"  Gated features enabled: {s['enabled_gated_count']} / "
        f"{s['gated_feature_count']}"
    )
    lines.append("")
    if s["tier"] == _lic.TIER_FREE:
        lines.append("  You are on the Free tier. All Free-tier features are available.")
        lines.append("  Upgrade path: https://tokenpak.ai/pricing   (coming soon)")
        lines.append("")
    elif s["status"] == "pending_validation":
        lines.append(
            "  ⏳ Pending validation — license key is stored but the validator "
            "is not yet live.\n"
            "     Free-tier features remain active in the meantime."
        )
        lines.append("")
    return "\n".join(lines)


def run_license(args: argparse.Namespace) -> int:
    """`tokenpak license` — show current license state."""
    s = _lic.summary_for_cli()
    if getattr(args, "as_json", False) or getattr(args, "json", False):
        print(json.dumps(s, indent=2))
        return 0
    print(_render_summary(s))
    return 0


def run_plan(args: argparse.Namespace) -> int:
    """`tokenpak plan` — show available plans + what the user has today."""
    s = _lic.summary_for_cli()
    if getattr(args, "as_json", False) or getattr(args, "json", False):
        print(json.dumps({
            "current": s,
            "plans": [
                {"tier": _lic.TIER_FREE, "label": _lic.describe_tier(_lic.TIER_FREE),
                 "price": "$0", "blurb": "Full Free-tier feature set — proxy, vault, compression, dashboard."},
                {"tier": _lic.TIER_PRO, "label": _lic.describe_tier(_lic.TIER_PRO),
                 "price": "TBD", "blurb": "Code/log/JSON compression, smart routing, session telemetry, trace + replay."},
                {"tier": _lic.TIER_TEAM, "label": _lic.describe_tier(_lic.TIER_TEAM),
                 "price": "TBD", "blurb": "Budget enforcement, OAuth, real-time stats API, shared vault, handoff system."},
                {"tier": _lic.TIER_ENTERPRISE, "label": _lic.describe_tier(_lic.TIER_ENTERPRISE),
                 "price": "TBD", "blurb": "A/B testing, shadow mode, regression detection, FinOps + audit pages, DLP/PII."},
            ],
        }, indent=2))
        return 0
    print("")
    print("  TOKENPAK plans")
    print("  " + "─" * 40)
    print(f"  You are on:  {s['tier_label']}  (status: {s['status']})")
    print("")
    print("  Available plans:")
    print("    Free     Proxy, vault, compression, web dashboard,")
    print("                   savings tracking. $0. Always available.")
    print("    Pro            Code/log/JSON compression, smart routing,")
    print("                   session telemetry, trace & replay, CSV/JSON")
    print("                   export. — coming soon")
    print("    Team           Budget enforcement, OAuth for API keys,")
    print("                   real-time stats API, shared vault,")
    print("                   handoff system. — coming soon")
    print("    Enterprise     A/B testing, shadow mode, regression")
    print("                   detection, FinOps + audit pages,")
    print("                   PII/DLP scanning. — coming soon")
    print("")
    print("  Upgrade:   https://tokenpak.ai/pricing   (coming soon)")
    print("  Activate:  tokenpak activate <your-key>")
    print("")
    return 0


def run_activate(args: argparse.Namespace) -> int:
    """`tokenpak activate <key>` — store a license key."""
    key = (getattr(args, "key", "") or "").strip()
    email = (getattr(args, "email", "") or "").strip()
    if not key:
        print("activate: provide a license key → tokenpak activate <key>")
        return 2
    result = _lic.activate(key, email=email)
    if not result.ok:
        print(f"✖ activate failed: {result.summary}")
        if result.error:
            print(f"  detail: {result.error}")
        return 1
    print("")
    print(f"  ✅ {result.summary}")
    if result.license and result.license.activated_at:
        print(f"  stored at: {_lic._license_path()}")
        print(f"  activated: {result.license.activated_at}")
    print("")
    return 0


def run_deactivate(args: argparse.Namespace) -> int:
    """`tokenpak deactivate` — revert to Free."""
    removed = _lic.deactivate()
    if removed:
        print("  ✅ License removed. Reverted to Free.")
    else:
        print("  (no license was installed — already on Free)")
    return 0
