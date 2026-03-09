"""tokenpak agent handoff — context handoff CLI commands.

Usage:
    tokenpak agent handoff create --from <agent> --to <agent> [options]
    tokenpak agent handoff receive <id>
    tokenpak agent handoff apply <id>
    tokenpak agent handoff list [--to <agent>] [--from <agent>] [--status <status>]
    tokenpak agent handoff show <id>
    tokenpak agent handoff expire
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Optional

SEP = "────────────────────────────────────"


def _fmt_time(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_ttl(seconds: float) -> str:
    if seconds <= 0:
        return "expired"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


def _print_handoff(h) -> None:
    """Print a single handoff in detail view."""
    status_icon = {
        "pending": "⏳",
        "received": "📥",
        "applied": "✅",
        "expired": "💀",
        "invalid": "❌",
    }.get(h.status.value, "?")

    print(f"\n{SEP}")
    print(f"  Handoff: {h.id}")
    print(f"  {status_icon} Status:  {h.status.value}")
    print(f"  From:    {h.from_agent}  →  To: {h.to_agent}")
    print(f"  Created: {_fmt_time(h.created_at)}")
    print(f"  Expires: {_fmt_time(h.expires_at)}  (TTL: {_fmt_ttl(h.ttl_remaining_s())})")
    if h.received_at:
        print(f"  Received:{_fmt_time(h.received_at)}")
    if h.applied_at:
        print(f"  Applied: {_fmt_time(h.applied_at)}")

    if h.summary:
        print(f"\n  Summary: {h.summary}")
    if h.what_was_done:
        print(f"\n  Done:    {h.what_was_done}")
    if h.whats_next:
        print(f"  Next:    {h.whats_next}")
    if h.relevant_files:
        print("\n  Relevant files:")
        for f in h.relevant_files:
            print(f"    • {f}")

    if h.context_refs:
        print(f"\n  Context refs ({len(h.context_refs)}):")
        for ref in h.context_refs:
            valid_tag = ""
            if ref.valid is True:
                valid_tag = " ✓"
            elif ref.valid is False:
                valid_tag = " ✗ (missing)"
            desc = f"  {ref.description}" if ref.description else ""
            print(f"    [{ref.type}] {ref.path}{valid_tag}{desc}")
    print(SEP)


def handoff_cmd(args) -> None:
    """Dispatch handoff subcommand."""
    from tokenpak.agent.agentic.handoff import ContextRef, HandoffManager, HandoffStatus

    manager = HandoffManager()
    subcmd = getattr(args, "handoff_cmd", None)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------
    if subcmd == "create":
        refs: List[ContextRef] = []
        for raw in getattr(args, "ref", None) or []:
            # Format: type:path[:description]
            parts = raw.split(":", 2)
            if len(parts) < 2:
                print(
                    f"✖ Bad ref format '{raw}' — use type:path or type:path:description",
                    file=sys.stderr,
                )
                sys.exit(1)
            rtype, rpath = parts[0], parts[1]
            rdesc = parts[2] if len(parts) > 2 else ""
            refs.append(ContextRef(type=rtype, path=rpath, description=rdesc))

        rel_files = getattr(args, "file", None) or []
        ttl = getattr(args, "ttl", 24) or 24

        try:
            h = manager.create_handoff(
                from_agent=args.handoff_from,
                to_agent=args.handoff_to,
                context_refs=refs,
                what_was_done=getattr(args, "done", "") or "",
                whats_next=getattr(args, "next", "") or "",
                relevant_files=rel_files,
                ttl_hours=float(ttl),
            )
        except ValueError as e:
            print(f"✖ {e}", file=sys.stderr)
            sys.exit(1)

        print("✅ Handoff created")
        print(f"   ID:   {h.id}")
        print(f"   From: {h.from_agent}  →  To: {h.to_agent}")
        print(f"   TTL:  {ttl}h  (expires {_fmt_time(h.expires_at)})")
        if h.summary:
            print(f"   Summary: {h.summary}")
        print(f"\n   Receive with:  tokenpak agent handoff receive {h.id}")

    # ------------------------------------------------------------------
    # receive
    # ------------------------------------------------------------------
    elif subcmd == "receive":
        try:
            h = manager.receive_handoff(args.handoff_id)
        except FileNotFoundError as e:
            print(f"✖ {e}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"✖ {e}", file=sys.stderr)
            sys.exit(1)

        invalid_refs = [r for r in h.context_refs if r.valid is False]
        if invalid_refs:
            print(f"⚠️  Handoff {h.id[:8]}… received with {len(invalid_refs)} missing ref(s)")
            for ref in invalid_refs:
                print(f"   ✗ [{ref.type}] {ref.path}")
        else:
            print(f"✅ Handoff {h.id[:8]}… received — {len(h.context_refs)} ref(s) validated")

        _print_handoff(h)
        print(f"\n   Apply with:  tokenpak agent handoff apply {h.id}")

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------
    elif subcmd == "apply":
        try:
            h = manager.apply_handoff(args.handoff_id)
        except FileNotFoundError as e:
            print(f"✖ {e}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"✖ {e}", file=sys.stderr)
            sys.exit(1)

        print(f"✅ Handoff {h.id[:8]}… applied")
        _print_handoff(h)

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------
    elif subcmd == "list":
        to_filter = getattr(args, "handoff_to", None)
        from_filter = getattr(args, "handoff_from", None)
        status_filter = None
        if getattr(args, "status", None):
            try:
                from tokenpak.agent.agentic.handoff import HandoffStatus

                status_filter = HandoffStatus(args.status)
            except ValueError:
                print(f"✖ Unknown status '{args.status}'", file=sys.stderr)
                sys.exit(1)

        handoffs = manager.list_handoffs(
            to_agent=to_filter,
            from_agent=from_filter,
            status=status_filter,
        )

        if not handoffs:
            print("(no handoffs found)")
            return

        status_icons = {
            "pending": "⏳",
            "received": "📥",
            "applied": "✅",
            "expired": "💀",
            "invalid": "❌",
        }
        print(f"\n{'ID':<38} {'STATUS':<10} {'FROM':<8} {'TO':<8} {'SUMMARY'}")
        print(SEP + SEP)
        for h in handoffs:
            icon = status_icons.get(h.status.value, "?")
            summary = (h.summary or "")[:50]
            print(
                f"{h.id:<38} {icon} {h.status.value:<8} {h.from_agent:<8} {h.to_agent:<8} {summary}"
            )

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------
    elif subcmd == "show":
        h = manager.get_handoff(args.handoff_id)  # type: ignore[assignment]
        if h is None:
            print(f"✖ Handoff '{args.handoff_id}' not found", file=sys.stderr)
            sys.exit(1)
        _print_handoff(h)

    # ------------------------------------------------------------------
    # expire
    # ------------------------------------------------------------------
    elif subcmd == "expire":
        count = manager.expire_stale()
        print(f"✅ Expired {count} stale handoff(s)")

    else:
        print("Usage: tokenpak agent handoff <create|receive|apply|list|show|expire>")
        sys.exit(1)
