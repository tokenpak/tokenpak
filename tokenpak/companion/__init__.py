# SPDX-License-Identifier: Apache-2.0
"""tokenpak companion — local pre-send optimizer for Claude Code TUI/CLI.

The companion runs alongside Claude Code as an MCP server + hook pipeline.
It optimizes what goes *into* each request — context pruning, token estimation,
cost simulation, session journaling, memory capsules — filling the gap where the
tokenpak proxy is forced into byte-preserved passthrough mode.

Scope:
    IN:  Claude Code TUI (primary), Claude Code CLI (secondary)
    OUT: API/SDK routes — those get full proxy pipeline (compression, compaction,
         proxy-managed caching) and don't need local optimization.

Quick start::

    # Launch Claude Code with companion active
    tokenpak claude

    # Or start companion subsystems individually
    tokenpak companion mcp-serve    # stdio MCP server
    tokenpak companion journal show # session journal viewer

Architecture::

    ┌─────────────────────────────────────────────────┐
    │  tokenpak companion                             │
    │                                                 │
    │  hooks/          ← UserPromptSubmit pipeline    │
    │    pre_send.py      token est → cost sim →      │
    │                     budget gate → journal write  │
    │                                                 │
    │  mcp/            ← MCP stdio server             │
    │    server.py        tools: estimate_tokens,      │
    │    tools.py         check_budget, load_capsule,  │
    │                     prune_context, journal_*     │
    │                                                 │
    │  transcript/     ← Claude Code transcript parser│
    │    parser.py        JSONL reader, token counter  │
    │    watcher.py       live session file discovery  │
    │                                                 │
    │  journal/        ← session journaling           │
    │    store.py         SQLite per-session + shared  │
    │    writer.py        auto-write from hooks/MCP    │
    │                                                 │
    │  capsules/       ← reusable memory capsules     │
    │    builder.py       build from transcript        │
    │    loader.py        load into conversation       │
    │                                                 │
    │  budget/         ← cost tracking + gating       │
    │    tracker.py       rolling cost tally           │
    │    gate.py          hook exit-2 budget blocker   │
    │                                                 │
    │  config.py       ← TOKENPAK_COMPANION_* env vars│
    │  launcher.py     ← `tokenpak claude` entry point│
    └─────────────────────────────────────────────────┘
"""

__all__ = [
    "launch",
    "CompanionConfig",
]


def launch(args: "list[str] | None" = None) -> "int":
    """Launch Claude Code with the companion active.

    Entry point for ``tokenpak claude [args]``.  Sets up the MCP server,
    hook pipeline, and system prompt, then ``exec``s into ``claude``.

    Returns the exit code (only reached if exec fails).
    """
    from .launcher import main as _main

    return _main(args)
