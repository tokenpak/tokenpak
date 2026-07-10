# SPDX-License-Identifier: Apache-2.0
"""Codex integration for the tokenpak companion.

Adapts the shared companion infrastructure (MCP server, budget tracker,
journal, capsules) for OpenAI Codex CLI.  Uses Codex-native integration
points: ``codex mcp add`` for tool registration, hooks.json for lifecycle
hooks, AGENTS.md for durable behavior, and skills for reusable workflows.
"""

__all__ = ["launch"]


def launch(
    args: "list[str] | None" = None,
    *,
    receipt_out: "str | None" = None,
    run_id: "str | None" = None,
) -> int:
    """Launch Codex with the companion active.

    Entry point for ``tokenpak codex [args]``.
    """
    from .launcher import main as _main

    return _main(args, receipt_out=receipt_out, run_id=run_id)
