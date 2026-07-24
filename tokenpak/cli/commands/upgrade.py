# SPDX-License-Identifier: Apache-2.0
"""CLI handler for ``tokenpak upgrade``."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

DEFAULT_UPGRADE_URL = "https://tokenpak.ai/pro"
UPGRADE_URL_ENV = "TOKENPAK_UPGRADE_URL"


def resolve_upgrade_url() -> str:
    """Return the configured upgrade URL, defaulting to the public Pro page."""
    return os.environ.get(UPGRADE_URL_ENV, DEFAULT_UPGRADE_URL)


def cmd_upgrade(args: argparse.Namespace) -> int:
    """Open the TokenPak Pro upgrade page in the user's default browser."""
    url = resolve_upgrade_url()

    if getattr(args, "print_url", False):
        print(url)
        return 0

    print(f"Opening {url} in your default browser ...")
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        opened = False
        print(f"  Could not launch a browser: {exc}", file=sys.stderr)

    if not opened:
        print()
        print("  If the browser did not open, visit the URL manually:")
        print(f"    {url}")

    return 0


def build_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register ``tokenpak upgrade`` on a subparsers action."""
    p = sub.add_parser(
        "upgrade",
        help="Open the TokenPak Pro upgrade page in your browser",
        description=(
            "Open the canonical TokenPak Pro upgrade page in your default "
            f"browser. Target URL is {DEFAULT_UPGRADE_URL} "
            f"(override with {UPGRADE_URL_ENV})."
        ),
    )
    p.add_argument(
        "--print-url",
        action="store_true",
        dest="print_url",
        help="Print the upgrade URL to stdout instead of opening a browser",
    )
    p.set_defaults(func=cmd_upgrade)
    return p


__all__ = [
    "DEFAULT_UPGRADE_URL",
    "UPGRADE_URL_ENV",
    "build_parser",
    "cmd_upgrade",
    "resolve_upgrade_url",
]
