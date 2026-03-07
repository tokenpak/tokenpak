"""TokenPak replay CLI commands — list, show, run, and clear captured sessions.

Exposes the replay subcommand for the tokenpak CLI.  The underlying
store lives in ``tokenpak.agent.telemetry.replay``; this module wires
argparse parsers and handlers.

Re-exported from the top-level ``tokenpak.cli`` module so that both
import paths work::

    from tokenpak.agent.cli.commands.replay import (
        cmd_replay_list,
        cmd_replay_show,
        cmd_replay_run,
        cmd_replay_clear,
        build_replay_parser,
    )

    # or via the legacy monolith:
    from tokenpak.cli import cmd_replay_list, cmd_replay_show, cmd_replay_run, cmd_replay_clear
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------


def _replay_store_path() -> str:
    """Return the default replay store path."""
    return str(Path.home() / ".tokenpak" / "replay.db")


def _get_replay_store():
    from tokenpak.agent.telemetry.replay import get_replay_store

    return get_replay_store(_replay_store_path())


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_replay_list(args):
    """List recent replay entries.

    Args:
        args: argparse Namespace with optional attributes:
            - limit (int): max entries to show (default 20)
            - provider (str|None): filter by provider
            - json (bool): output raw JSON
    """
    store = _get_replay_store()
    limit = getattr(args, "limit", 20) or 20
    provider = getattr(args, "provider", None)
    as_json = getattr(args, "json", False)

    entries = store.list(limit=limit, provider=provider)
    if not entries:
        print("No replay entries found.  Run tokenpak via the proxy to capture sessions.")
        return

    if as_json:
        print(json.dumps([e.to_dict() for e in entries], indent=2, default=str))
        return

    header = (
        f"{'':2} {'ID':<10} {'Timestamp':<20} {'Provider/Model':<30} {'Tokens':>12} {'Saved':>6}"
    )
    print(header)
    print("─" * len(header))
    for e in entries:
        ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        pm = f"{e.provider}/{e.model}"
        tokens_str = f"{e.input_tokens_raw}→{e.input_tokens_sent}"
        has_content = "📦" if e.messages is not None else "  "
        print(
            f"{has_content} {e.replay_id:<10} {ts:<20} {pm:<30} {tokens_str:>12} {e.savings_pct:>6.1f}%"
        )
    print(
        f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}  (📦 = content captured, eligible for replay)"
    )


def cmd_replay_show(args):
    """Show details of a single replay entry.

    Args:
        args: argparse Namespace with:
            - id (str): replay entry ID
            - messages (bool): print captured messages if available
    """
    store = _get_replay_store()
    entry_id = getattr(args, "id", None)
    show_messages = getattr(args, "messages", False)

    if not entry_id:
        print("Error: replay entry ID required.", file=sys.stderr)
        sys.exit(1)

    e = store.get(entry_id)
    if e is None:
        print(f"Error: replay entry '{entry_id}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Replay Entry: {e.replay_id}")
    print(f"  Timestamp : {e.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Provider  : {e.provider}")
    print(f"  Model     : {e.model}")
    print(f"  Tokens In : {e.input_tokens_raw} raw → {e.input_tokens_sent} sent")
    print(f"  Saved     : {e.tokens_saved} tokens ({e.savings_pct:.1f}%)")
    print(f"  Cost      : ${e.cost_usd:.6f}")
    if e.metadata:
        print(f"  Metadata  : {json.dumps(e.metadata)}")
    if e.messages is None:
        print("\n  (No content captured — replay not available)")
    elif show_messages:
        print(f"\n  Messages ({len(e.messages)}):")
        for i, m in enumerate(e.messages, 1):
            role = m.get("role", "?")
            content = str(m.get("content", ""))
            preview = content[:120] + "…" if len(content) > 120 else content
            print(f"    [{i}] {role}: {preview}")
    else:
        print(f"\n  Content captured: {len(e.messages)} message(s).  Use --messages to print.")


def cmd_replay_run(args):
    """Replay a captured session, optionally with a different model.

    Args:
        args: argparse Namespace with:
            - id (str): replay entry ID
            - model (str|None): override model
            - diff (bool): show diff between original and replay
            - no_compress (bool): skip compression on replay
    """
    store = _get_replay_store()
    entry_id = getattr(args, "id", None)
    new_model = getattr(args, "model", None)
    show_diff = getattr(args, "diff", False)
    no_compress = getattr(args, "no_compress", False)

    if not entry_id:
        print("Error: replay entry ID required.", file=sys.stderr)
        sys.exit(1)

    e = store.get(entry_id)
    if e is None:
        print(f"Error: replay entry '{entry_id}' not found.", file=sys.stderr)
        sys.exit(1)

    if e.messages is None:
        print(f"Entry {args.id} has no captured messages — cannot replay.")
        sys.exit(1)

    target_model = new_model or e.model
    compress = not no_compress

    print(f"Replaying [{e.replay_id}] — original: {e.provider}/{e.model}")
    print(f"  Target model : {target_model}")
    print(f"  Compression  : {'off' if no_compress else 'on'}")
    print(f"  Messages     : {len(e.messages)}")

    # Estimate token savings for the replay
    if compress:
        original_tokens = e.input_tokens_raw
        # Estimate: same compression ratio as original
        ratio = e.input_tokens_sent / e.input_tokens_raw if e.input_tokens_raw else 1.0
        estimated_sent = int(original_tokens * ratio)
        estimated_saved = original_tokens - estimated_sent
        print(f"  Est. sent    : ~{estimated_sent} tokens (saved ~{estimated_saved})")
    else:
        print(f"  Est. sent    : ~{e.input_tokens_raw} tokens (no compression)")

    if show_diff:
        print("\n  Diff vs. original:")
        print(f"    Original model : {e.provider}/{e.model}")
        print(f"    Replay model   : {target_model}")
        if new_model:
            print("    Model changed  : ✓")
        if no_compress:
            orig_saved = e.tokens_saved
            print(f"    Tokens delta   : +{orig_saved} (compression disabled)")

    print("\n  ✓ Replay metadata prepared.  Connect to proxy to execute.")


def cmd_replay_clear(args):
    """Clear all entries from the replay store.

    Args:
        args: argparse Namespace (no required attributes)
    """
    store = _get_replay_store()
    n = store.clear()
    print(f"Cleared {n} replay entr{'y' if n == 1 else 'ies'} from store.")


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------


def build_replay_parser(sub):
    """Register the ``replay`` subcommand under an argparse subparsers object.

    Args:
        sub: argparse _SubParsersAction to add the replay parser to.

    Returns:
        The replay ArgumentParser for further customisation.
    """
    p_replay = sub.add_parser("replay", help="List, inspect, and re-run captured sessions")
    rsub = p_replay.add_subparsers(dest="replay_cmd")

    # list
    p_list = rsub.add_parser("list", help="Show recent captured sessions")
    p_list.add_argument("--limit", type=int, default=20, metavar="N")
    p_list.add_argument("--provider", metavar="NAME")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_replay_list)

    # show
    p_show = rsub.add_parser("show", help="Show details of a captured session")
    p_show.add_argument("id", help="Replay entry ID")
    p_show.add_argument("--messages", action="store_true", help="Print captured messages")
    p_show.set_defaults(func=cmd_replay_show)

    # run
    p_run = rsub.add_parser("run", help="Re-run a captured session")
    p_run.add_argument("id", help="Replay entry ID")
    p_run.add_argument("--model", metavar="MODEL", help="Override model for replay")
    p_run.add_argument("--diff", action="store_true", help="Show diff vs. original")
    p_run.add_argument(
        "--no-compress",
        dest="no_compress",
        action="store_true",
        help="Disable compression on replay",
    )
    p_run.set_defaults(func=cmd_replay_run)

    # clear
    p_clear = rsub.add_parser("clear", help="Remove all entries from the replay store")
    p_clear.set_defaults(func=cmd_replay_clear)

    def _replay_dispatch(args):
        if getattr(args, "func", None):
            args.func(args)
        else:
            cmd_replay_list(args)

    p_replay.set_defaults(func=_replay_dispatch)
    return p_replay


# Alias for consistency with CLI test imports
_build_replay_parser = build_replay_parser
