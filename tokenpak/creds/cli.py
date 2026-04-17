# SPDX-License-Identifier: Apache-2.0
"""``tokenpak creds`` subcommands.

MVP surface: ``list`` + ``doctor``. ``add/remove/test/route`` ship in
the next slice once the discovery + hazard layer has caught the
ownership bugs.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from .doctor import Issue, run as doctor_run
from .model import Credential, KIND_OAUTH
from .providers import discover_all


def _age_or_expiry(cred: Credential, now: int) -> str:
    if cred.kind != KIND_OAUTH or cred.expires_at is None:
        return "-"
    delta = cred.expires_at - now
    if delta < 0:
        days = abs(delta) // 86400
        if days >= 1:
            return f"EXPIRED {days}d ago"
        return "EXPIRED"
    if delta < 3600:
        return f"expires {delta // 60}m"
    if delta < 86400:
        return f"expires {delta // 3600}h"
    return f"expires {delta // 86400}d"


def _format_expiry_display(cred: Credential) -> str:
    if cred.expires_at is None:
        return "-"
    try:
        return datetime.fromtimestamp(cred.expires_at, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "-"


def cmd_list(args) -> int:
    """Render discovered credentials as a padded table."""
    creds = discover_all()
    if not creds:
        print("no credentials found", file=sys.stderr)
        return 0

    now = int(time.time())
    rows: list[list[str]] = [
        ["ID", "PLATFORM", "KIND", "REFRESH", "EXPIRES", "STATUS", "SOURCE"]
    ]
    for c in creds:
        rows.append(
            [
                c.id,
                c.platform,
                c.kind,
                c.refresh_owner,
                _format_expiry_display(c),
                _age_or_expiry(c, now),
                c.source,
            ]
        )

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row))
        print(line.rstrip())
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(widths))))
    return 0


def cmd_doctor(args) -> int:
    """Run hazard checks and print a grouped report. Non-zero on any error."""
    creds = discover_all()
    issues = doctor_run(creds)

    print(f"discovered {len(creds)} credentials from {len({c.provider for c in creds})} providers")

    if not issues:
        print("no issues")
        return 0

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warn"]

    def _print(label: str, items: list[Issue]) -> None:
        if not items:
            return
        print(f"\n{label} ({len(items)}):")
        width = max(len(i.subject) for i in items)
        for i in items:
            print(f"  [{i.severity.upper():5}] {i.subject.ljust(width)}  {i.detail}")

    _print("errors", errors)
    _print("warnings", warnings)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    sub = args[0] if args else "list"
    rest = args[1:]

    if sub in ("list", "ls"):
        return cmd_list(rest)
    if sub == "doctor":
        return cmd_doctor(rest)

    print(f"tokenpak creds: unknown subcommand {sub!r}", file=sys.stderr)
    print("usage: tokenpak creds [list|doctor]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
