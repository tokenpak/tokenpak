# SPDX-License-Identifier: Apache-2.0
"""MCP tool definitions and handlers.

Each tool is a (schema, handler) pair.  The server dispatches by name.
Adding a new tool = adding one entry to TOOLS + one handler function.

Design principle: tools are stateless functions that receive the shared
CompanionState and return a JSON-serializable result.  State mutation
goes through CompanionState methods so it's centralized and testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import CompanionConfig


@dataclass
class CompanionState:
    """Shared mutable state for the MCP server process.

    Lives for the duration of the Claude Code session.  All tools receive
    this and can read/mutate it.
    """

    config: CompanionConfig = field(default_factory=CompanionConfig.from_env)
    call_count: int = 0
    session_id: str = ""
    transcript_path: str = ""

    # Lazy-initialized subsystems
    _budget_tracker: Any = None
    _journal_store: Any = None

    @property
    def budget_tracker(self) -> Any:
        if self._budget_tracker is None:
            from ..budget.tracker import BudgetTracker
            self._budget_tracker = BudgetTracker(
                db_path=self.config.journal_dir / "budget.db",
                daily_budget=self.config.budget_daily_usd,
            )
        return self._budget_tracker

    @property
    def journal_store(self) -> Any:
        if self._journal_store is None:
            from ..journal.store import JournalStore
            self._journal_store = JournalStore(
                db_path=self.config.journal_dir / "journal.db",
            )
        return self._journal_store


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    """MCP tool definition."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[CompanionState, dict[str, Any]], str]


def _handle_estimate_tokens(state: CompanionState, args: dict[str, Any]) -> str:
    """Estimate token count for text or a file."""
    text = args.get("text", "")
    file_path = args.get("file_path", "")

    if file_path:
        p = Path(file_path)
        if p.exists():
            text = p.read_text(errors="replace")
        else:
            return json.dumps({"error": f"File not found: {file_path}"})

    chars = len(text)

    # Tiktoken with chunking for large texts (avoids OOM / LRU cache thrashing)
    try:
        from tokenpak.telemetry.tokens import count_tokens
        CHUNK = 100_000  # chars per chunk — keeps LRU cache effective
        if chars <= CHUNK:
            tokens = count_tokens(text)
        else:
            tokens = sum(
                count_tokens(text[i:i + CHUNK])
                for i in range(0, chars, CHUNK)
            )
        method = "tiktoken"
    except Exception:
        tokens = chars // 4
        method = "heuristic (chars/4)"

    return json.dumps({
        "tokens": tokens,
        "chars": chars,
        "method": method,
        "source": file_path or "inline text",
    }, indent=2)


def _handle_check_budget(state: CompanionState, args: dict[str, Any]) -> str:
    """Check remaining budget for this session/day."""
    tracker = state.budget_tracker
    est = tracker.estimate(input_tokens=0)
    return json.dumps({
        "session_cost_usd": est.session_total_usd,
        "daily_cost_usd": est.daily_total_usd,
        "daily_budget_usd": est.daily_budget_usd,
        "remaining_usd": est.budget_remaining_usd,
        "session_requests": tracker.session_requests,
        "budget_set": est.daily_budget_usd > 0,
    }, indent=2)


def _handle_load_capsule(state: CompanionState, args: dict[str, Any]) -> str:
    """Load a memory capsule from a prior session."""
    session_id = args.get("session_id", "")
    capsule_dir = state.config.journal_dir / "capsules"

    if session_id:
        # Load specific capsule
        from ..capsules.builder import load_capsule
        for p in capsule_dir.glob("*.md"):
            if session_id in p.stem:
                content = load_capsule(str(p))
                if content:
                    return content
        return json.dumps({"error": f"No capsule found for session {session_id}"})

    # List available capsules
    if not capsule_dir.exists():
        return json.dumps({"capsules": [], "message": "No capsules stored yet."})

    capsules = []
    for p in sorted(capsule_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
        capsules.append({
            "session_id": p.stem,
            "size_bytes": p.stat().st_size,
            "modified": p.stat().st_mtime,
        })
    return json.dumps({"capsules": capsules}, indent=2)


def _handle_prune_context(state: CompanionState, args: dict[str, Any]) -> str:
    """Summarize verbose content to reduce token count.

    This is the heuristic-only path.  It truncates and deduplicates rather
    than doing LLM summarization (which would cost tokens).
    """
    text = args.get("text", "")
    max_tokens = args.get("max_tokens", 2000)

    if not text:
        return json.dumps({"error": "No text provided"})

    original_chars = len(text)
    original_tokens_est = original_chars // 4

    # Strategy: truncate to max_tokens * 4 chars with word boundary
    max_chars = max_tokens * 4
    if len(text) > max_chars:
        # Keep first 60% and last 30%, elide middle
        head_len = int(max_chars * 0.6)
        tail_len = int(max_chars * 0.3)
        head = text[:head_len].rsplit(" ", 1)[0] if " " in text[:head_len] else text[:head_len]
        tail = text[-tail_len:].split(" ", 1)[-1] if " " in text[-tail_len:] else text[-tail_len:]
        elided = original_chars - len(head) - len(tail)
        text = f"{head}\n\n[... {elided:,} chars elided ...]\n\n{tail}"

    pruned_tokens_est = len(text) // 4
    return json.dumps({
        "pruned_text": text,
        "original_tokens": original_tokens_est,
        "pruned_tokens": pruned_tokens_est,
        "reduction_pct": round((1 - pruned_tokens_est / max(original_tokens_est, 1)) * 100, 1),
    })


def _handle_journal_read(state: CompanionState, args: dict[str, Any]) -> str:
    """Read journal entries for the current or a past session."""
    target = args.get("session_id", state.session_id)
    entry_type = args.get("entry_type")
    limit = args.get("limit", 20)

    if not target:
        # List recent sessions
        sessions = state.journal_store.recent_sessions(limit=10)
        return json.dumps({
            "sessions": [
                {
                    "session_id": s.session_id,
                    "project_dir": s.project_dir,
                    "total_requests": s.total_requests,
                    "total_cost_usd": s.total_cost_usd,
                    "entry_count": s.entry_count,
                }
                for s in sessions
            ]
        }, indent=2)

    entries = state.journal_store.get_entries(target, entry_type=entry_type, limit=limit)
    return json.dumps({
        "session_id": target,
        "entries": [
            {
                "timestamp": e.timestamp,
                "type": e.entry_type,
                "content": e.content,
            }
            for e in entries
        ],
    }, indent=2)


def _handle_journal_write(state: CompanionState, args: dict[str, Any]) -> str:
    """Add a note to the current session journal."""
    content = args.get("content", "")
    if not content:
        return json.dumps({"error": "No content provided"})

    session_id = state.session_id
    if not session_id:
        return json.dumps({"error": "No active session"})

    state.journal_store.add_entry(
        session_id=session_id,
        entry_type="user",
        content=content,
    )
    return json.dumps({"status": "ok", "session_id": session_id})


def _handle_session_info(state: CompanionState, args: dict[str, Any]) -> str:
    """Return companion status and session stats."""
    return json.dumps({
        "companion_version": "0.1.0",
        "session_id": state.session_id,
        "call_count": state.call_count,
        "config": {
            "profile": state.config.profile,
            "budget_daily_usd": state.config.budget_daily_usd,
            "hooks_enabled": state.config.hooks_enabled,
            "prune_threshold": state.config.prune_threshold,
        },
        "budget": {
            "session_cost": state.budget_tracker.session_cost,
            "session_requests": state.budget_tracker.session_requests,
        },
    }, indent=2)


# ---------------------------------------------------------------------------
# Tool registry — add new tools here
# ---------------------------------------------------------------------------

TOOLS: list[ToolDef] = [
    ToolDef(
        name="estimate_tokens",
        description="Estimate token count for text or a file. Use before reading large files or including verbose context to decide if it's worth the cost.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to estimate tokens for"},
                "file_path": {"type": "string", "description": "Path to a file (alternative to text)"},
            },
        },
        handler=_handle_estimate_tokens,
    ),
    ToolDef(
        name="check_budget",
        description="Check remaining cost budget for this session and today. Call before starting expensive multi-step tasks.",
        input_schema={"type": "object", "properties": {}},
        handler=_handle_check_budget,
    ),
    ToolDef(
        name="load_capsule",
        description="Load a memory capsule from a prior session. Call when resuming work or when the user references past sessions. Omit session_id to list available capsules.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to load (omit to list available)"},
            },
        },
        handler=_handle_load_capsule,
    ),
    ToolDef(
        name="prune_context",
        description="Compress verbose text (large tool outputs, error logs) to reduce token usage. Keeps the beginning and end, elides the middle.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to prune"},
                "max_tokens": {"type": "integer", "description": "Target token count (default 2000)", "default": 2000},
            },
            "required": ["text"],
        },
        handler=_handle_prune_context,
    ),
    ToolDef(
        name="journal_read",
        description="Read journal entries for this session or a past session. Omit session_id to list recent sessions.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session to query (default: current)"},
                "entry_type": {"type": "string", "description": "Filter by type: auto, user, milestone, cost"},
                "limit": {"type": "integer", "description": "Max entries to return (default 20)", "default": 20},
            },
        },
        handler=_handle_journal_read,
    ),
    ToolDef(
        name="journal_write",
        description="Add a note to the current session journal. Use for important decisions, milestones, or context the user might want later.",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The journal note to save"},
            },
            "required": ["content"],
        },
        handler=_handle_journal_write,
    ),
    ToolDef(
        name="session_info",
        description="Get companion status, session stats, and configuration.",
        input_schema={"type": "object", "properties": {}},
        handler=_handle_session_info,
    ),
]
