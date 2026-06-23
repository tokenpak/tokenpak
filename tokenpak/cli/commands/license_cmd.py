# SPDX-License-Identifier: Apache-2.0
"""CLI handlers for `tokenpak license`, `tokenpak plan`, `tokenpak activate`,
`tokenpak deactivate`. Free-tier today; Pro/Team/Enterprise surface ready.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from tokenpak import licensing as _lic
from tokenpak.cli.commands.upgrade import DEFAULT_UPGRADE_URL


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
        lines.append(f"  Upgrade path: {DEFAULT_UPGRADE_URL}")
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
    """`tokenpak plan` — show available plans + what the user has today.

    Catalog is discovered dynamically from the gate table + an optional
    ``<TOKENPAK_HOME>/pricing.json`` file. No hardcoded list, no
    misleading ``"TBD"`` strings.
    """
    s = _lic.summary_for_cli()
    plans = _lic.discover_plans()
    if getattr(args, "as_json", False) or getattr(args, "json", False):
        print(json.dumps({"current": s, "plans": plans}, indent=2))
        return 0
    print("")
    print("  TOKENPAK plans")
    print("  " + "─" * 40)
    print(f"  You are on:  {s['tier_label']}  (status: {s['status']})")
    print("")
    print("  Available plans:")
    for plan in plans:
        price = plan["price"]
        suffix = "" if price not in ("unannounced", "") else "  — pricing not yet announced"
        print(f"    {plan['label']:<11}  {price:<10}  ({plan['feature_count']} gated features){suffix}")
        if plan["blurb"]:
            print(f"               {plan['blurb']}")
    print("")
    print("  Use:")
    print("    tokenpak features            see every feature + entitlement state")
    print("    tokenpak activate <key>      install a paid license key")
    print("")
    return 0


def run_activate(args: argparse.Namespace) -> int:
    """`tokenpak activate <key>` — store a license key.

    Per Beta 1 hardening (Packet G), this rejects obviously invalid
    inputs (empty, too short, wrong charset, placeholder strings) and
    surfaces a non-zero exit so scripts / CI don't silently treat a
    bad activation as success.
    """
    import sys as _sys

    key = (getattr(args, "key", "") or "").strip()
    email = (getattr(args, "email", "") or "").strip()
    if not key:
        print("activate: provide a license key → tokenpak activate <key>",
              file=_sys.stderr)
        _sys.exit(2)
    result = _lic.activate(key, email=email)
    if not result.ok:
        print(f"✖ activate failed: {result.summary}", file=_sys.stderr)
        if result.error:
            print(f"  detail: {result.error}", file=_sys.stderr)
        _sys.exit(1)
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
