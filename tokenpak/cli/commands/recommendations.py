# SPDX-License-Identifier: Apache-2.0
"""tokenpak recommendations — telemetry-driven action surface (TIP-07).

Thin CLI wrapper around :mod:`tokenpak.telemetry.recommendations`. The
engine does all the work; this module only handles argv parsing and output
selection so the CLI stays a presentation layer per Standard 01 §1.3
(entrypoints are thin).
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from tokenpak.telemetry.recommendations import (
    RecommendationsEngine,
    format_human,
    format_json,
)

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([hHdD]?)\s*$")


def parse_window(value: Optional[str]) -> int:
    """Parse window strings like ``24h``, ``7d``, ``12`` into hours."""
    if value is None:
        return 24
    m = _WINDOW_RE.match(str(value))
    if not m:
        raise ValueError(
            f"invalid --window value: {value!r} (expected like '24h' or '7d')"
        )
    n = int(m.group(1))
    if n <= 0:
        raise ValueError(f"--window must be positive, got {value!r}")
    unit = (m.group(2) or "h").lower()
    return n * 24 if unit == "d" else n


def cmd_recommendations(args: argparse.Namespace) -> int:
    """Dispatch handler for ``tokenpak recommendations``."""
    try:
        window_hours = parse_window(getattr(args, "window", "24h"))
    except ValueError as exc:
        print(
            f"✗ tokenpak recommendations — {exc}",
            file=sys.stderr,
        )
        return 2

    engine = RecommendationsEngine(db_path=getattr(args, "db_path", None))
    result = engine.run(
        window_hours=window_hours,
        model=getattr(args, "model", None),
        platform=getattr(args, "platform", None),
    )

    if getattr(args, "as_json", False):
        print(format_json(result))
    else:
        sys.stdout.write(format_human(result))
    return 0


def build_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register ``tokenpak recommendations`` on a subparsers action."""
    p = sub.add_parser(
        "recommendations",
        help="Show ranked, telemetry-backed recommendations",
        description=(
            "Show ranked, telemetry-backed recommendations from the local "
            "TokenPak telemetry store. Reads only — never modifies traffic."
        ),
    )
    p.add_argument(
        "--window",
        default="24h",
        help="Rolling window (e.g. 24h, 7d). Default: 24h",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Filter recommendations to a single model name",
    )
    p.add_argument(
        "--platform",
        default=None,
        help="Filter recommendations to a single platform (matched against agent_id and payload)",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    p.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Override telemetry DB path (default: resolved via tokenpak.core.paths.get_db_path)",
    )
    p.set_defaults(func=cmd_recommendations)
    return p


__all__ = ["build_parser", "cmd_recommendations", "parse_window"]
