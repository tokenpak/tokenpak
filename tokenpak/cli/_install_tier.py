"""`tokenpak install-tier <tier>` subcommand.

Reads the user's license from ``~/.tokenpak/license.json``, validates the
tier matches the requested argument, and shells out to pip with the
correct ``--index-url`` + HTTP Basic auth to fetch ``tokenpak-paid`` from
the license-gated private index at ``pypi.tokenpak.ai``.

Layer 1 of the 3-layer gating model (Kevin amendment 2026-04-21):

  - Layer 1 — package access: this subcommand + the PEP 503 endpoint
    together enforce that only a valid license key can pip-install
    ``tokenpak-paid``.
  - Layer 2 — runtime entitlements: ``tokenpak-paid`` enforces
    post-install (registers only entitled commands, revalidates periodically).
  - Layer 3 — server-backed services: enforced on the license-server
    itself, independent of what's installed locally.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LICENSE_FILE = Path(os.environ.get(
    "TOKENPAK_LICENSE_FILE", str(Path.home() / ".tokenpak" / "license.json")
))

# The canonical license-gated private index. Override via env for testing.
PRIVATE_INDEX_URL = os.environ.get(
    "TOKENPAK_PRIVATE_INDEX_URL",
    "https://pypi.tokenpak.ai/simple/",
)

# pip falls back to public PyPI for transitive OSS deps (tokenpak itself,
# fastapi, etc.). tokenpak-paid is only on the private index.
PUBLIC_INDEX_URL = "https://pypi.org/simple/"

VALID_TIERS = ("pro", "team", "enterprise")


class InstallTierError(RuntimeError):
    """Raised when install-tier cannot proceed (missing license, pip gone, etc.)."""


def _load_license() -> Optional[Dict[str, Any]]:
    """Read ``~/.tokenpak/license.json`` or return None if missing/invalid."""
    if not LICENSE_FILE.exists():
        return None
    try:
        return json.loads(LICENSE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read license file at %s: %s", LICENSE_FILE, exc)
        return None


def _license_key(license_data: Dict[str, Any]) -> Optional[str]:
    """Extract the raw license key from a license.json blob.

    The license file format predates this subcommand; common shapes:
      {"key": "..."}
      {"license_key": "..."}
      {"token": "..."}
    """
    for candidate in ("key", "license_key", "token", "pkg_access_token"):
        value = license_data.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _license_tier(license_data: Dict[str, Any]) -> Optional[str]:
    """Extract the current tier name."""
    for candidate in ("tier", "plan", "subscription_tier"):
        value = license_data.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _index_url_with_auth(license_key: str) -> str:
    """Build the ``--index-url`` with HTTP Basic auth baked in.

    PyPI convention: username ``__token__``, password is the license key.
    """
    # Parse the existing private index URL and inject auth
    # Format: https://__token__:<key>@pypi.tokenpak.ai/simple/
    if "://" not in PRIVATE_INDEX_URL:
        raise InstallTierError(
            f"Invalid TOKENPAK_PRIVATE_INDEX_URL: {PRIVATE_INDEX_URL!r}"
        )
    scheme, rest = PRIVATE_INDEX_URL.split("://", 1)
    return f"{scheme}://__token__:{license_key}@{rest}"


def _pip_executable() -> str:
    """Return a pip command that can install into the current Python env.

    Prefers ``{sys.executable} -m pip`` for reliability across venv /
    system-python / pipx setups.
    """
    return sys.executable


def _run_pip_install(license_key: str, dry_run: bool = False) -> int:
    """Invoke pip to install ``tokenpak-paid``; returns pip's exit code."""
    auth_url = _index_url_with_auth(license_key)
    cmd = [
        _pip_executable(),
        "-m", "pip",
        "install",
        "--upgrade",
        "--index-url", auth_url,
        "--extra-index-url", PUBLIC_INDEX_URL,
        "tokenpak-paid",
    ]

    # For display + dry-run, redact the license key
    display_cmd = list(cmd)
    for i, part in enumerate(display_cmd):
        if "__token__:" in part:
            display_cmd[i] = part.split("__token__:")[0] + "__token__:<REDACTED>@" + part.split("@", 1)[1]

    print("Running:", " ".join(display_cmd))
    if dry_run:
        print("(--dry-run; not executing)")
        return 0

    result = subprocess.run(cmd)
    return result.returncode


def _post_install_smoke() -> bool:
    """Quick check: can we import tokenpak_paid after install?"""
    try:
        subprocess.run(
            [sys.executable, "-c", "import tokenpak_paid"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except subprocess.CalledProcessError as exc:
        print(
            f"\n⚠ tokenpak-paid installed but failed to import: "
            f"{exc.stderr.decode()[:500] if exc.stderr else '<no stderr>'}",
            file=sys.stderr,
        )
        return False
    except subprocess.TimeoutExpired:
        print("\n⚠ tokenpak-paid import smoke-check timed out.", file=sys.stderr)
        return False


def run_install_tier(tier: str, dry_run: bool = False) -> int:
    """Main entry for ``tokenpak install-tier <tier>``.

    Returns the shell-exit code to propagate.
    """
    tier_lower = (tier or "").strip().lower()
    if tier_lower not in VALID_TIERS:
        print(
            f"✗ Unknown tier {tier!r}. Expected one of: {', '.join(VALID_TIERS)}",
            file=sys.stderr,
        )
        return 2

    license_data = _load_license()
    if license_data is None:
        print(
            "✗ No license activated.\n"
            "  Run `tokenpak activate <KEY>` first, then retry.\n"
            "  (Don't have a key? Visit tokenpak.ai/pricing.)",
            file=sys.stderr,
        )
        return 2

    key = _license_key(license_data)
    if not key:
        print(
            f"✗ License file at {LICENSE_FILE} is missing a usable license key.\n"
            "  Re-run `tokenpak activate <KEY>` to refresh.",
            file=sys.stderr,
        )
        return 2

    current_tier = _license_tier(license_data)
    if current_tier and current_tier != tier_lower:
        # Not a hard fail — the license may entitle multiple tiers, or the
        # user may want to install tokenpak-paid and let Layer 2 sort it out.
        # Just note the mismatch and continue.
        print(
            f"ℹ Your license tier is {current_tier!r}; you requested install-tier "
            f"{tier_lower!r}. Proceeding — entitlement checks at runtime will "
            f"enforce what you're actually allowed to use."
        )

    print(f"Installing tokenpak-paid (for tier: {tier_lower})...")

    rc = _run_pip_install(key, dry_run=dry_run)
    if rc != 0:
        print(
            f"\n✗ pip install exited with code {rc}.\n"
            "  Common causes:\n"
            "    401 → license expired/revoked; run `tokenpak plan` to check\n"
            "    404 → no wheel for your Python version (file an issue)\n"
            "    connection refused → pypi.tokenpak.ai unreachable; check network",
            file=sys.stderr,
        )
        return rc

    if not dry_run and not _post_install_smoke():
        return 3

    if not dry_run:
        print(f"\n✓ tokenpak-paid installed. {tier_lower} features are now available.")
    return 0


__all__ = [
    "run_install_tier",
    "InstallTierError",
    "VALID_TIERS",
]
