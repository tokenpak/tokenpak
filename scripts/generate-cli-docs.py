#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""C4 (PM/GTM v2 Phase 2): auto-generate docs/reference/cli.md from the CLI.

Walks `_COMMAND_GROUPS` in `tokenpak.cli._impl` (the display grouping used
by `tokenpak help`) and introspects each subcommand's argparse parser via
`build_parser()`. Emits a deterministic markdown document at
``docs/reference/cli.md``. Bytes-stable across runs — suitable for a
CI gate that diffs generated vs committed output.

Usage:
    python3 scripts/generate-cli-docs.py             # writes docs/reference/cli.md
    python3 scripts/generate-cli-docs.py --check     # exits non-zero if stale
    python3 scripts/generate-cli-docs.py --stdout    # prints to stdout only

Traces to v2 M-C4 (Axis C trust artifacts) per
~/vault/02_COMMAND_CENTER/initiatives/2026-04-23-tokenpak-pm-gtm-readiness-v2/.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

# Repo-relative output target. Adjust together with `.github/workflows/ci.yml`
# `cli-docs-in-sync` if you move the file.
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "reference" / "cli.md"

HEADER = """\
# TokenPak CLI Reference

> **Generated** by `scripts/generate-cli-docs.py` from the current
> `_COMMAND_GROUPS` + argparse registrations in `tokenpak/cli/_impl.py`.
> Do not edit by hand — the `cli-docs-in-sync` CI gate fails on any
> diff between the generator output and this file.
>
> Run `python3 scripts/generate-cli-docs.py` after CLI changes to refresh.

This document lists every `tokenpak <subcommand>` registered at build
time, grouped the way `tokenpak help` groups them.

"""


def _subcommand_help(parser: argparse.ArgumentParser, name: str) -> str:
    """Return the full ``tokenpak <name> --help`` output for the named subcommand."""
    subparsers_action = None
    for action in parser._actions:  # noqa: SLF001 — argparse has no public accessor
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            subparsers_action = action
            break
    if subparsers_action is None:
        return "(no subparsers registered)"

    sub = subparsers_action.choices.get(name)
    if sub is None:
        return f"(subcommand `{name}` not registered in argparse)"

    buf = io.StringIO()
    with redirect_stdout(buf):
        sub.print_help()
    return buf.getvalue().rstrip()


def build_markdown() -> str:
    """Return the canonical CLI-reference markdown."""
    # Deferred import so `--help` works before the tokenpak package is usable.
    from tokenpak.cli._impl import _COMMAND_GROUPS, build_parser

    parser = build_parser()
    lines: list[str] = [HEADER.rstrip() + "\n"]

    # Table of contents — stable order = insertion order of the dict.
    lines.append("## Groups\n")
    for group_name in _COMMAND_GROUPS:
        anchor = group_name.lower().replace(" ", "-")
        lines.append(f"- [{group_name}](#{anchor})")
    lines.append("")

    for group_name, commands in _COMMAND_GROUPS.items():
        lines.append(f"## {group_name}\n")
        for cmd_name, one_liner in commands:
            lines.append(f"### `tokenpak {cmd_name}`\n")
            lines.append(f"{one_liner}\n")
            help_text = _subcommand_help(parser, cmd_name)
            lines.append("```")
            lines.append(help_text)
            lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate docs/reference/cli.md from the tokenpak CLI registry."
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the committed file differs from generator output.",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="Print generated markdown to stdout; do not write the file.",
    )
    args = ap.parse_args()

    generated = build_markdown()

    if args.stdout:
        sys.stdout.write(generated)
        return 0

    if args.check:
        if not OUTPUT_PATH.exists():
            print(
                f"cli-docs-in-sync: {OUTPUT_PATH} is missing. "
                f"Run: python3 scripts/generate-cli-docs.py",
                file=sys.stderr,
            )
            return 1
        committed = OUTPUT_PATH.read_text(encoding="utf-8")
        if committed != generated:
            print(
                "cli-docs-in-sync: docs/reference/cli.md is out of date.\n"
                "Run: python3 scripts/generate-cli-docs.py\n"
                "Then commit the updated file.",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
