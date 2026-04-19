"""
tokenpak-admin — Admin CLI for license key management.

Commands:
    keygen       Generate a signed license key
    verify       Verify a license token (requires public key)
    genkeys      Generate a fresh RSA-4096 keypair and save to disk

Usage:
    tokenpak-admin keygen --tier pro --seats 1 --customer "sha256hash"
    tokenpak-admin keygen --tier enterprise --days 365 --output ./license.key
    tokenpak-admin verify --token "TPAK..." --public-key ./tokenpak_pub.pem
    tokenpak-admin genkeys --out-dir ./keys
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .keys import (
    LicensePayload,
    format_license_key,
    generate_keypair,
    sign_license,
)
from .validator import LicenseValidator

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _load_private_pem(path: Optional[str]) -> bytes:
    """Load private key from file or TOKENPAK_PRIVATE_KEY env var."""
    if path:
        return Path(path).read_bytes()
    env_key = os.environ.get("TOKENPAK_PRIVATE_KEY", "")
    if env_key:
        return env_key.encode()
    print(
        "ERROR: No private key provided. Use --private-key <path> "
        "or set TOKENPAK_PRIVATE_KEY env var.",
        file=sys.stderr,
    )
    sys.exit(1)


def _load_public_pem(path: Optional[str]) -> bytes:
    """Load public key from file or TOKENPAK_PUBLIC_KEY env var."""
    if path:
        return Path(path).read_bytes()
    env_key = os.environ.get("TOKENPAK_PUBLIC_KEY", "")
    if env_key:
        return env_key.encode()
    print(
        "ERROR: No public key provided. Use --public-key <path> "
        "or set TOKENPAK_PUBLIC_KEY env var.",
        file=sys.stderr,
    )
    sys.exit(1)


# ─────────────────────────────────────────────
# Command: keygen
# ─────────────────────────────────────────────


def cmd_keygen(args: argparse.Namespace) -> None:
    """Generate a signed license key."""
    private_pem = _load_private_pem(args.private_key)

    # Validate tier
    valid_tiers = {"oss", "pro", "team", "enterprise"}
    if args.tier not in valid_tiers:
        print(
            f"ERROR: Unknown tier {args.tier!r}. Valid: {', '.join(sorted(valid_tiers))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Compute expiry
    expires_at = None
    if args.days and args.days > 0:
        expiry_dt = datetime.now(timezone.utc) + timedelta(days=args.days)
        expires_at = expiry_dt.isoformat()

    # Hash customer string if provided (SHA-256, so PII never stored raw)
    customer_id = None
    if args.customer:
        customer_id = hashlib.sha256(args.customer.encode()).hexdigest()

    payload = LicensePayload(
        key_id=format_license_key(),
        tier=args.tier,
        seats=args.seats,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at,
        features=args.features or [],
        customer_id=customer_id,
    )

    token = sign_license(payload, private_pem)

    # Print summary
    print("=" * 60)
    print("  TokenPak License Key Generated")
    print("=" * 60)
    print(f"  Key ID  : {payload.key_id}")
    print(f"  Tier    : {payload.tier}")
    print(f"  Seats   : {payload.seats if payload.seats > 0 else 'unlimited'}")
    print(f"  Expires : {expires_at or 'perpetual'}")
    print(f"  Customer: {customer_id or 'n/a'}")
    if payload.features:
        print(f"  Extra   : {', '.join(payload.features)}")
    print()
    print("TOKEN:")
    print(token)
    print()

    if args.output:
        out = Path(args.output)
        out.write_text(token)
        print(f"Token written to: {out}")

    if args.json:
        result = {
            "key_id": payload.key_id,
            "tier": payload.tier,
            "seats": payload.seats,
            "expires_at": expires_at,
            "customer_id": customer_id,
            "token": token,
        }
        print("\nJSON:")
        print(json.dumps(result, indent=2))


# ─────────────────────────────────────────────
# Command: verify
# ─────────────────────────────────────────────


def cmd_verify(args: argparse.Namespace) -> None:
    """Verify a license token."""
    public_pem = _load_public_pem(args.public_key)

    token_str = args.token
    if not token_str and args.token_file:
        token_str = Path(args.token_file).read_text().strip()
    if not token_str:
        print("ERROR: Provide --token or --token-file", file=sys.stderr)
        sys.exit(1)

    validator = LicenseValidator(public_pem=public_pem)
    result = validator.validate(token_str)

    print("=" * 60)
    print("  TokenPak License Verification")
    print("=" * 60)
    print(f"  Status   : {result.status.value.upper()}")
    print(f"  Tier     : {result.tier.value}")
    print(f"  Usable   : {'YES' if result.is_usable else 'NO'}")
    print(f"  Seats    : {result.seats if result.seats > 0 else 'unlimited'}")
    if result.seats > 0:
        print(f"  Used     : {result.seats_used}")
    print(f"  Expires  : {result.expires_at or 'perpetual'}")
    print(f"  Message  : {result.message}")
    print()
    print(f"  Features : {', '.join(result.features)}")

    if not result.is_usable:
        sys.exit(2)


# ─────────────────────────────────────────────
# Command: genkeys
# ─────────────────────────────────────────────


def cmd_genkeys(args: argparse.Namespace) -> None:
    """Generate a fresh RSA-4096 keypair."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    priv_path = out_dir / "tokenpak_private.pem"
    pub_path = out_dir / "tokenpak_public.pem"

    if priv_path.exists() and not args.force:
        print(f"ERROR: {priv_path} already exists. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    print("Generating RSA-4096 keypair (this may take a few seconds)...")
    private_pem, public_pem = generate_keypair()

    priv_path.write_bytes(private_pem)
    pub_path.write_bytes(public_pem)

    print(f"✅ Private key: {priv_path}  (KEEP SECRET — never commit this)")
    print(f"✅ Public key : {pub_path}   (embed in agent)")
    print()
    print("Next steps:")
    print("  1. Store private key securely (e.g., env TOKENPAK_PRIVATE_KEY)")
    print("  2. Embed public key in agent (TOKENPAK_PUBLIC_KEY or bake into binary)")
    print("  3. Run: tokenpak-admin keygen --tier pro --seats 1 --customer <id>")


# ─────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokenpak-admin",
        description="TokenPak admin tool — license key management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── keygen ─────────────────────────────────
    kg = sub.add_parser("keygen", help="Generate a signed license key")
    kg.add_argument("--tier", required=True, help="oss|pro|team|enterprise")
    kg.add_argument("--seats", type=int, default=0, help="Seat count (0 = unlimited)")
    kg.add_argument("--days", type=int, default=365, help="Days until expiry (0 = perpetual)")
    kg.add_argument("--customer", default=None, help="Customer identifier (hashed before storage)")
    kg.add_argument("--features", nargs="*", default=[], help="Extra feature flags")
    kg.add_argument("--private-key", default=None, help="Path to private PEM key")
    kg.add_argument("--output", "-o", default=None, help="Write token to file")
    kg.add_argument("--json", action="store_true", help="Also emit JSON summary")

    # ── verify ─────────────────────────────────
    vr = sub.add_parser("verify", help="Verify a license token")
    vr.add_argument("--token", default=None, help="Token string to verify")
    vr.add_argument("--token-file", default=None, help="File containing token")
    vr.add_argument("--public-key", default=None, help="Path to public PEM key")

    # ── genkeys ─────────────────────────────────
    gk = sub.add_parser("genkeys", help="Generate a fresh RSA-4096 keypair")
    gk.add_argument("--out-dir", default="./keys", help="Directory to write keys into")
    gk.add_argument("--force", action="store_true", help="Overwrite existing keys")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "keygen":
        cmd_keygen(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "genkeys":
        cmd_genkeys(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
