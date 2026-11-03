# SPDX-License-Identifier: Apache-2.0
"""Local operator approval and content-free escalation export."""

from __future__ import annotations

__all__: list[str] = []

import json
import sqlite3
import sys
from argparse import Namespace, _SubParsersAction

from tokenpak.cli.exit_codes import EXIT_CORRUPT_STATE, EXIT_FAILURE


def register_parser(subparsers: _SubParsersAction) -> None:
    parser = subparsers.add_parser("spend-guard", help="Review unattended Spend Guard blocks")
    commands = parser.add_subparsers(dest="spend_guard_command", required=True)
    approve = commands.add_parser("approve", help="Approve one exact held request locally")
    approve.add_argument("pending_id", help="Pending id from the unattended block")
    approve.add_argument("--json", action="store_true", help="Print structured output")
    approve.set_defaults(func=run)
    events = commands.add_parser("events", help="Read content-free escalation events as JSON")
    events.add_argument("--after", type=int, default=0, help="Exclusive event id cursor")
    events.add_argument("--limit", type=int, default=100, help="Maximum events (1-1000)")
    events.add_argument("--json", action="store_true", help="Print structured output (default)")
    events.set_defaults(func=run)


def run(args: Namespace) -> None:
    from tokenpak.proxy.spend_guard.audit import write_audit
    from tokenpak.proxy.spend_guard.pending import PendingStore
    from tokenpak.proxy.spend_guard.policy import load_config
    from tokenpak.proxy.spend_guard.unattended import events

    cfg = load_config()
    try:
        if args.spend_guard_command == "events":
            if args.after < 0 or not 1 <= args.limit <= 1000:
                _error(args, "Use --after >= 0 and --limit between 1 and 1000.", EXIT_FAILURE)
            rows = events(cfg.audit_db_path, after=args.after, limit=args.limit)
            print(
                json.dumps(
                    {"events": rows, "next_cursor": rows[-1]["event_id"] if rows else args.after}
                )
            )
            return
        store = PendingStore(cfg.audit_db_path)
        if not store.approve_unattended(args.pending_id):
            _error(
                args,
                "No approval recorded: the unattended hold is missing, expired, "
                "already approved, or already resolved. Review the original block and its expiry.",
                EXIT_FAILURE,
            )
        pending = store.get_by_id(args.pending_id)
        write_audit(
            cfg.audit_db_path,
            event_type="operator_approve",
            session_id=pending.session_id,
            pending_id=pending.pending_id,
            reason="operator_approval_once",
        )
        result = {
            "pending_id": pending.pending_id,
            "approved": True,
            "expires_at": pending.expires_at,
            "requests_allowed": 1,
            "provider_called": False,
        }
        if args.json:
            print(json.dumps(result))
        else:
            print(
                f"Approved {pending.pending_id} for one identical retry before expiry. "
                "No request was sent; resume the original session with its held request."
            )
    except (OSError, sqlite3.Error, ValueError) as exc:
        _error(
            args,
            f"Spend Guard state could not be read or updated ({type(exc).__name__}). "
            "Check the configured guard database with tokenpak doctor.",
            EXIT_CORRUPT_STATE,
        )


def _error(args: Namespace, message: str, code: int) -> None:
    if args.json:
        print(json.dumps({"error": {"message": message, "exit_code": code}}))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(code)
