"""MCP tool handlers for the tokenpak companion.

Each handler follows the MCP tool-call convention:

    handler(args: Dict[str, Any], **kwargs) -> Dict[str, Any]

The return value always contains a ``"content"`` key with a string payload.
On error it additionally contains an ``"error"`` key.

Currently exposed tools
-----------------------
load_capsule
    List or load session capsules from ``~/.tokenpak/companion/capsules/``.
estimate_tokens
    Stub — Wave 2 will implement real token counting.
check_budget
    Stub — Wave 2 will implement real budget tracking.
prune_context
    Stub — Wave 2 will implement real context pruning.
journal_read
    Stub — Wave 2 will implement real journal reads.
journal_write
    Stub — Wave 2 will implement real journal writes.
session_info
    Stub — Wave 2 will implement real session metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tokenpak.companion.capsules.builder import load_capsule as _load_capsule


def handle_load_capsule(
    args: Dict[str, Any],
    capsule_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """MCP handler for the ``load_capsule`` tool.

    Behaviour:
    * ``args`` contains no ``session_id`` (or it is falsy) → returns a
      newline-separated list of available capsule IDs as ``content``.
    * ``args["session_id"]`` is set → returns the full markdown of that
      capsule as ``content``.

    Args:
        args: Tool arguments dict.  Recognised key: ``session_id`` (str, optional).
        capsule_dir: Override the capsule directory.  Defaults to
            ``~/.tokenpak/companion/capsules/``.

    Returns:
        ``{"content": <str>}`` on success, or ``{"content": "", "error": <str>}``
        when the requested capsule does not exist.
    """
    session_id: Optional[str] = args.get("session_id") or None

    try:
        result = _load_capsule(session_id=session_id, capsule_dir=capsule_dir)
        return {"content": result}
    except FileNotFoundError as exc:
        return {"content": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# Stub handlers — Wave 2 replaces these with real logic
# ---------------------------------------------------------------------------

def handle_estimate_tokens(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: return 0 token count. Wave 2 adds real tiktoken counting."""
    return {"content": "0"}


def handle_check_budget(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: return zeroed budget. Wave 2 adds real session tracking."""
    return {"content": '{"session_tokens": 0, "daily_tokens": 0, "budget_tokens": 0}'}


def handle_prune_context(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: return text unchanged. Wave 2 adds heuristic pruning."""
    return {"content": args.get("text", "")}


def handle_journal_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: return empty entries list. Wave 2 adds real DB reads."""
    return {"content": "[]"}


def handle_journal_write(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: acknowledge write without persisting. Wave 2 adds real DB writes."""
    return {"content": "ok"}


def handle_session_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stub: return empty session metadata. Wave 2 adds real session context."""
    return {"content": '{"session_id": null, "companion_version": "0.1.0"}'}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "load_capsule": handle_load_capsule,
    "estimate_tokens": handle_estimate_tokens,
    "check_budget": handle_check_budget,
    "prune_context": handle_prune_context,
    "journal_read": handle_journal_read,
    "journal_write": handle_journal_write,
    "session_info": handle_session_info,
}


# ---------------------------------------------------------------------------
# Tool schema (MCP tool-list entry)
# ---------------------------------------------------------------------------

LOAD_CAPSULE_SCHEMA: Dict[str, Any] = {
    "name": "load_capsule",
    "description": (
        "List available session capsules or load a specific one. "
        "Call with no arguments to list all capsule IDs. "
        "Call with session_id to retrieve the full capsule markdown."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session ID to load.  Omit to list all available capsules.",
            }
        },
        "required": [],
    },
}
