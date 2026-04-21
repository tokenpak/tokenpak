"""
tokenpak license — License management commands.

Subcommands:
    tokenpak activate <key>   Validate + store a license key
    tokenpak deactivate       Remove license, revert to OSS
    tokenpak plan             Show current tier, expiry, seats, features

Legacy (no subcommand):
    tokenpak license          Show edition/license info (OSS)
"""

from __future__ import annotations

import sys

# ─────────────────────────────────────────────
# Plain-Python runner (no click)
# ─────────────────────────────────────────────


def _run_activate(token: str) -> None:
    from tokenpak.agent.license.activation import activate

    try:
        result = activate(token)
        print("✅ License activated!")
        print(f"   Tier    : {result.tier.value}")
        print(f"   Expires : {result.expires_at or 'perpetual'}")
        print(f"   Seats   : {result.seats if result.seats > 0 else 'unlimited'}")
        print(f"   Status  : {result.status.value}")
    except ValueError as exc:
        print(f"❌ Activation failed: {exc}", file=sys.stderr)
        sys.exit(1)


def _run_deactivate() -> None:
    from tokenpak.agent.license.activation import deactivate

    deactivate()
    print("✅ License removed. Reverted to OSS (free).")


def _run_plan() -> None:
    from tokenpak.agent.license.activation import get_plan

    result = get_plan()
    print("TOKENPAK  |  Plan")
    print("────────────────────────")
    print()
    if result.tier.value == "oss":
        print("  Tier    : OSS (free)")
        print("  Status  : Active")
        print("  Expires : —")
    else:
        print(f"  Tier    : {result.tier.value.upper()}")
        print(f"  Status  : {result.status.value.upper()}")
        print(f"  Expires : {result.expires_at or 'perpetual'}")
        grace = result.grace_expires_at
        if grace:
            print(f"  Grace   : {grace}")
        seats = result.seats
        if seats > 0:
            print(f"  Seats   : {result.seats_used} / {seats}")
        else:
            print("  Seats   : unlimited")
    print()
    print("  Features:")
    for feat in sorted(result.features):
        print(f"    • {feat}")
    print()
    if not result.is_usable:
        print(f"  ⚠️  {result.message}")


def run() -> None:
    """Print license and edition info (legacy — no subcommand)."""
    print("TOKENPAK  |  License")
    print("────────────────────────")
    print()
    print("  Edition:   OSS (Community)")
    print("  License:   Apache 2.0")
    print()
    print("  Manage your license:")
    print("    tokenpak activate <key>   — activate a Pro/Team/Enterprise license")
    print("    tokenpak deactivate       — remove license, revert to OSS")
    print("    tokenpak plan             — show current tier and features")
    print()
    print("  https://github.com/anthropics/tokenpak")


# ─────────────────────────────────────────────
# Click commands (if available)
# ─────────────────────────────────────────────

try:
    import click

    @click.command("license")
    def license_cmd():
        """Show license and edition info."""
        run()

    @click.command("activate")
    @click.argument("key")
    def activate_cmd(key: str):
        """Activate a Pro/Team/Enterprise license key."""
        _run_activate(key)

    @click.command("deactivate")
    def deactivate_cmd():
        """Remove license key and revert to OSS."""
        _run_deactivate()

    @click.command("plan")
    def plan_cmd():
        """Show current license tier, expiry, seats, and features."""
        _run_plan()

except ImportError:
    pass
