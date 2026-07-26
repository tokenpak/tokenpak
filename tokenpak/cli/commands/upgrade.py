# SPDX-License-Identifier: Apache-2.0
"""CLI handler for ``tokenpak upgrade`` — a hidden compatibility shim.

Public Pro enrollment is not available, so there is nothing for this command
to open. It previously launched a browser at ``https://tokenpak.ai/pro``, which
returns HTTP 404 — and it was advertised in top-level help and echoed by a
footer on every ``tokenpak status`` run, so the product's most-displayed
call-to-action dead-ended.

The verb is retained, hidden from discovery, so existing scripts and muscle
memory get a clear answer instead of "unknown command". It does not open a
browser and it does not print a URL unless one has been explicitly configured
via ``TOKENPAK_UPGRADE_URL`` — an operator opt-in for private cohort
onboarding, never a default.
"""

from __future__ import annotations

import argparse
import os

UPGRADE_URL_ENV = "TOKENPAK_UPGRADE_URL"

#: No default. A proactive Pro call-to-action is suppressed product-wide, so
#: there is deliberately no built-in URL to fall back to.
DEFAULT_UPGRADE_URL = ""

_UNAVAILABLE_MESSAGE = (
    "Public TokenPak Pro enrollment is not available.\n"
    "\n"
    "There is no signup page to open and no plan to purchase right now.\n"
    "Everything TokenPak does today is available in the edition you have."
)


def resolve_upgrade_url() -> str:
    """Return an operator-configured upgrade URL, or an empty string.

    Empty means "no enrollment destination exists" — callers must treat that
    as "print nothing", not as "fall back to a default page".
    """
    return os.environ.get(UPGRADE_URL_ENV, "").strip()


def upgrade_cta_line() -> str:
    """The upgrade call-to-action line, or ``""`` when none should be shown.

    Every surface that wants to mention upgrading routes through this so the
    suppression holds in one place. Returns non-empty only when an operator has
    explicitly configured a destination.
    """
    url = resolve_upgrade_url()
    if not url:
        return ""
    return f"  TokenPak Pro: {url}"


def cmd_upgrade(args: argparse.Namespace) -> int:
    """Report that public Pro enrollment is unavailable. Opens nothing."""
    url = resolve_upgrade_url()

    if getattr(args, "print_url", False):
        # Print only a configured URL. Printing a fabricated default would
        # re-create the dead link this shim exists to remove.
        if url:
            print(url)
            return 0
        return 1

    print(_UNAVAILABLE_MESSAGE)
    if url:
        print()
        print(f"Your environment configures an enrollment URL: {url}")
    return 0


def build_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register ``tokenpak upgrade`` on a subparsers action, hidden from help."""
    p = sub.add_parser(
        "upgrade",
        help=argparse.SUPPRESS,
        description=(
            "Compatibility shim. Public TokenPak Pro enrollment is not "
            "available, so this command opens nothing and reports that state. "
            f"If {UPGRADE_URL_ENV} is set, its value is shown."
        ),
    )
    p.add_argument(
        "--print-url",
        action="store_true",
        dest="print_url",
        help=f"Print {UPGRADE_URL_ENV} if it is set; exit non-zero when it is not",
    )
    p.set_defaults(func=cmd_upgrade)
    return p


__all__ = [
    "DEFAULT_UPGRADE_URL",
    "UPGRADE_URL_ENV",
    "build_parser",
    "cmd_upgrade",
    "resolve_upgrade_url",
    "upgrade_cta_line",
]
