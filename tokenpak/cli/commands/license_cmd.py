# SPDX-License-Identifier: Apache-2.0
"""CLI handlers for `tokenpak license`, `tokenpak plan`, `tokenpak activate`,
`tokenpak deactivate`. Free-tier today; Pro surface ready.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from tokenpak import licensing as _lic
from tokenpak.cli.commands.upgrade import upgrade_cta_line


def _render_summary(s: dict[str, Any]) -> str:
    edition = _lic.public_edition(s["tier"])
    lines = [""]
    lines.append(f"  TOKENPAK license — {edition}")
    lines.append("  " + "─" * 40)
    lines.append(f"  Edition   {edition}")
    lines.append(f"  Status    {s['status']}")
    if s.get("email"):
        lines.append(f"  Email     {s['email']}")
    if s.get("activated_at"):
        lines.append(f"  Activated {s['activated_at']}")
    if s.get("expires_at"):
        lines.append(f"  Expires   {s['expires_at']}")
    if s.get("has_key"):
        lines.append(f"  Key       stored ({s['license_path']})")
        # Entitlement tier is metadata about a verified key, shown only when a
        # key exists. It is not an offering the reader could have chosen.
        lines.append(f"  Entitlement  {_lic.internal_tier_label(s['tier'])} (from license)")
    else:
        lines.append("  Key       (none)")
    lines.append("")
    if s["tier"] == _lic.TIER_FREE:
        # Previously "Gated features enabled: 0 / 52", which framed the edition
        # most users run as zero percent of the product.
        lines.append(f"  {edition} includes the proxy, compression, vault, dashboard,")
        lines.append("  and savings tracking. Everything shipped today is available.")
        cta = upgrade_cta_line()
        if cta:
            lines.append(cta)
        lines.append("")
    elif s["status"] == "pending_validation":
        lines.append(
            "  ⏳ Pending validation — license key is stored but the validator "
            "is not yet live.\n"
            f"     {_lic.EDITION_BASE} features remain active in the meantime."
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
    print("  TOKENPAK editions")
    print("  " + "─" * 40)
    print(f"  You are on:  {_lic.public_edition(s['tier'])}  (status: {s['status']})")
    print("")
    print("  Editions:")
    for plan in plans:
        # Price is intentionally omitted rather than shown as "unannounced":
        # public Pro enrollment is unavailable, so quoting a price field at all
        # implies a purchase path that does not exist.
        print(f"    {plan['label']:<14}  ({plan['feature_count']} gated features)")
        if plan["blurb"]:
            print(f"                    {plan['blurb']}")
    print("")
    print("  TokenPak Pro is not publicly available yet.")
    print("")
    print("  Use:")
    print("    tokenpak features            see every feature + entitlement state")
    print("    tokenpak activate <key>      install a license key you were issued")
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
        print("activate: provide a license key → tokenpak activate <key>", file=_sys.stderr)
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
