"""
Trial CLI commands for TokenPak.

Commands:
    tokenpak trial start       Start a new 14-day Pro trial
    tokenpak trial status      Check trial status and remaining days
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests

from .activation import _load_stored_token, activate, get_plan

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _get_device_fingerprint() -> str:
    """Generate device fingerprint from hostname + first MAC address."""
    import socket

    hostname = socket.gethostname()

    # Try to get first MAC address
    try:
        import uuid

        mac = uuid.getnode()
        mac_hex = format(mac, "012x")
        mac_str = ":".join(mac_hex[i : i + 2] for i in range(0, 12, 2))
    except Exception:
        mac_str = "unknown"

    fingerprint_str = f"{hostname}:{mac_str}"
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()


def _get_license_server_url() -> str:
    """Get license server URL from env or default."""
    return os.environ.get("TOKENPAK_LICENSE_SERVER", "http://localhost:8900")


# ─────────────────────────────────────────────
# Command: trial start
# ─────────────────────────────────────────────


def cmd_trial_start(args: Optional[object] = None) -> None:
    """Start a new 14-day Pro trial on this device."""
    device_fp = _get_device_fingerprint()
    server_url = _get_license_server_url()

    email = None
    if args and hasattr(args, "email"):
        email = args.email

    try:
        # Call license server /trial endpoint
        response = requests.post(
            urljoin(server_url, "/trial"),
            json={
                "device_fingerprint": device_fp,
                "email": email,
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        if response.status_code == 400:
            error_data = response.json().get("detail", {})
            if error_data.get("error") == "trial_already_used":
                print("❌ Trial already used on this device.")
                print("   " + error_data.get("message", ""))
                sys.exit(1)
        print(f"ERROR: Failed to start trial: {e}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    key = data["key"]

    # Activate the trial license locally
    try:
        result = activate(key)
        print("🎉 Pro trial activated!")
        print(f"   Key: {key}")
        print(f"   Expires: {result.expires_at}")
        print("   Duration: 14 days of full Pro access")
    except Exception as e:
        print(f"ERROR: Failed to activate trial key: {e}", file=sys.stderr)
        sys.exit(1)


# ─────────────────────────────────────────────
# Command: trial status
# ─────────────────────────────────────────────


def cmd_trial_status(args: Optional[object] = None) -> None:
    """Check trial status and remaining days."""
    token = _load_stored_token()

    if not token:
        print("❌ No license installed.")
        print("   Start a trial: tokenpak trial start")
        sys.exit(1)

    plan = get_plan()

    if not plan.is_usable:
        print("❌ License invalid or expired.")
        print(f"   {plan.message}")
        sys.exit(1)

    # Calculate days remaining
    if plan.expires_at:
        expires = datetime.fromisoformat(plan.expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc).replace(tzinfo=expires.tzinfo)
        days_remaining = (expires - now).days + 1  # +1 for today
        days_remaining = max(0, days_remaining)
    else:
        days_remaining = None

    # Check if it's a trial (from env hint or expiry)
    is_trial = os.environ.get("TOKENPAK_TRIAL", "").lower() == "true"

    if is_trial or (days_remaining and days_remaining <= 14):
        print("✨ Pro trial active")
        print(f"   Days remaining: {days_remaining}")
        print(f"   Expires: {plan.expires_at}")
        if days_remaining <= 3:
            print("   ⚠️  Trial expires soon. Upgrade now: tokenpak.dev/pricing")
    elif plan.tier.value in ("pro", "team", "enterprise"):
        print("✅ Pro license active")
        print(f"   Tier: {plan.tier.value}")
        if days_remaining:
            print(f"   Days remaining: {days_remaining}")
        print(f"   Expires: {plan.expires_at}")
    else:
        print(f"ℹ️  Current tier: {plan.tier.value}")
        print("   Upgrade to Pro: tokenpak.dev/pricing")


# ─────────────────────────────────────────────
# CLI Entry
# ─────────────────────────────────────────────


def setup_trial_cli(subparsers):
    """Register trial subcommand."""
    trial_parser = subparsers.add_parser(
        "trial",
        help="Manage trial licenses",
        description="Start or check status of your 14-day Pro trial.",
    )

    trial_sub = trial_parser.add_subparsers(dest="trial_cmd", required=True)

    # trial start
    start_parser = trial_sub.add_parser(
        "start",
        help="Start a 14-day Pro trial on this device",
    )
    start_parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Optional email for trial tracking",
    )
    start_parser.set_defaults(func=cmd_trial_start)

    # trial status
    status_parser = trial_sub.add_parser(
        "status",
        help="Check trial status and remaining days",
    )
    status_parser.set_defaults(func=cmd_trial_status)

    return trial_parser
