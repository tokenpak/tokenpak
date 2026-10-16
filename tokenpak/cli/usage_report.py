"""Local read-only usage report; no proxy connection or model request."""

import json
import sqlite3
import sys

from tokenpak._paths import monitor_db
from tokenpak.cli.exit_codes import EXIT_CORRUPT_STATE
from tokenpak.telemetry.job_usage import read_usage


def print_usage(args) -> None:
    """Print only usage counters and identifiers from the selected local ledger."""
    path = getattr(args, "db", None) or monitor_db(mode="read")
    try:
        result = read_usage(
            path,
            work_tag=getattr(args, "work_tag", None),
            session_id=getattr(args, "session_id", None),
        )
    except (sqlite3.Error, OSError):
        print(
            "TokenPak could not read the usage ledger. Check the database path and permissions.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CORRUPT_STATE) from None
    print(json.dumps(result, indent=2))
