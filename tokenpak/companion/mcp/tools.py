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

# Default input-rate for cost estimation when no model hint is available.
# Matches pre_send.py's fallback (sonnet input rate). Kept local rather than
# imported so tools.py has no cross-module coupling with the hook.
_COMPANION_DEFAULT_INPUT_RATE_USD_PER_MTOK = 3.0


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
                    # Record a savings event. Conservative: we don't know the
                    # raw-context baseline the capsule replaces, so log the
                    # capsule size as the delivered cost but 0 tokens_avoided.
                    # The *event* is still useful for by_tool counts.
                    if state.session_id:
                        try:
                            capsule_tokens = len(content) // 4
                            state.journal_store.record_savings(
                                session_id=state.session_id,
                                tool="load_capsule",
                                tokens_avoided=0,
                                cost_avoided_usd=0.0,
                                extra={
                                    "capsule_session": p.stem,
                                    "capsule_tokens_est": capsule_tokens,
                                },
                            )
                        except Exception:
                            pass
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
    tokens_avoided = max(0, original_tokens_est - pruned_tokens_est)
    cost_avoided = tokens_avoided * _COMPANION_DEFAULT_INPUT_RATE_USD_PER_MTOK / 1_000_000

    # Persist the savings so `tokenpak status` can attribute prompt-side value.
    # Best-effort: never fail the tool call on journal write errors.
    if tokens_avoided > 0 and state.session_id:
        try:
            state.journal_store.record_savings(
                session_id=state.session_id,
                tool="prune_context",
                tokens_avoided=tokens_avoided,
                cost_avoided_usd=cost_avoided,
                extra={"max_tokens": max_tokens},
            )
        except Exception:
            pass

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
# Vault access — exposes V1/V4/V6/V8 Free features as MCP tools.
# Lazy-imports VaultIndex so companion startup stays fast even without a vault.
# ---------------------------------------------------------------------------

import os as _os

_VAULT_INDEX_SINGLETON: Any = None
_VAULT_INDEX_ATTEMPTED: bool = False


def _vault_dir_candidates() -> list[Path]:
    """Where to look for a .tokenpak/ index directory, in priority order."""
    env = _os.environ.get("TOKENPAK_VAULT_DIR")
    out: list[Path] = []
    if env:
        out.append(Path(env))
    out.extend([
        Path.home() / "vault" / ".tokenpak",
        Path.home() / ".tokenpak" / "vault",
    ])
    return out


def _get_vault_index() -> Any:
    """Return a loaded VaultIndex singleton or None if no index is available."""
    global _VAULT_INDEX_SINGLETON, _VAULT_INDEX_ATTEMPTED
    if _VAULT_INDEX_SINGLETON is not None:
        return _VAULT_INDEX_SINGLETON
    if _VAULT_INDEX_ATTEMPTED:
        return None
    _VAULT_INDEX_ATTEMPTED = True
    try:
        from tokenpak.vault.retrieval.vault_index import VaultIndex
    except Exception:
        return None
    for cand in _vault_dir_candidates():
        if not (cand / "index.json").exists():
            continue
        try:
            vi = VaultIndex(str(cand))
            vi.maybe_reload()
            if vi.available:
                _VAULT_INDEX_SINGLETON = vi
                return vi
        except Exception:
            continue
    return None


def _handle_vault_search(state: CompanionState, args: dict[str, Any]) -> str:
    """Search the vault by BM25 and return top-K blocks with scores."""
    query = str(args.get("query", "")).strip()
    if not query:
        return json.dumps({"error": "query is required"})
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))

    vi = _get_vault_index()
    if vi is None:
        return json.dumps({
            "error": "vault_index_unavailable",
            "hint": (
                "No .tokenpak/index.json found. Run `tokenpak index <path>` "
                "or set TOKENPAK_VAULT_DIR to point at the index directory."
            ),
        })

    try:
        results = vi.search(query, top_k=limit)
    except Exception as exc:
        return json.dumps({"error": f"search_failed: {exc}"})

    out = []
    for block, score in results:
        block_id = block.get("block_id") or block.get("id") or ""
        out.append({
            "block_id": block_id,
            "path": block.get("path", ""),
            "score": round(float(score), 3),
            "tokens": int(block.get("tokens", 0) or 0),
            "preview": (block.get("title") or block.get("summary") or "")[:200],
        })
    return json.dumps({"query": query, "count": len(out), "results": out}, indent=2)


def _handle_vault_retrieve(state: CompanionState, args: dict[str, Any]) -> str:
    """Fetch a full vault block by block_id (exact) or path substring."""
    block_id = str(args.get("block_id", "")).strip()
    path_hint = str(args.get("path", "")).strip()
    if not block_id and not path_hint:
        return json.dumps({"error": "provide block_id or path"})

    vi = _get_vault_index()
    if vi is None:
        return json.dumps({"error": "vault_index_unavailable"})

    target_id: str | None = None
    if block_id and block_id in vi.blocks:
        target_id = block_id
    elif path_hint:
        # Fall back to substring match on paths
        for bid, meta in vi.blocks.items():
            if path_hint in (meta.get("path", "") or "") or path_hint in bid:
                target_id = bid
                break

    if target_id is None:
        return json.dumps({
            "error": "block_not_found",
            "block_id": block_id or None,
            "path": path_hint or None,
        })

    # Read content from the blocks dir (VaultIndex stores metadata-only in memory)
    try:
        blocks_dir = Path(vi.tokenpak_dir) / "blocks"
        content_path = blocks_dir / f"{target_id}.txt"
        content = content_path.read_text(errors="replace") if content_path.exists() else ""
    except Exception as exc:
        return json.dumps({"error": f"read_failed: {exc}"})

    meta = vi.blocks.get(target_id, {})
    return json.dumps({
        "block_id": target_id,
        "path": meta.get("path", ""),
        "tokens": int(meta.get("tokens", 0) or 0),
        "content": content,
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
    ToolDef(
        name="vault_search",
        description=(
            "Search the indexed vault by BM25 and return top-K matching blocks "
            "with relevance scores. Use when the user references project docs, "
            "code, or knowledge stored in the local vault. The proxy also "
            "auto-injects vault context, but this tool lets you query "
            "explicitly (e.g. narrowing to a specific concept)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (words or phrase)"},
                "limit": {"type": "integer", "description": "Max results (default 5, max 20)", "default": 5},
            },
            "required": ["query"],
        },
        handler=_handle_vault_search,
    ),
    ToolDef(
        name="vault_retrieve",
        description=(
            "Fetch the full content of a specific vault block by block_id "
            "(exact match, from vault_search results) or by path substring "
            "(first match). Returns content + metadata."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "Exact block_id from vault_search"},
                "path": {"type": "string", "description": "Path substring to match (alternative to block_id)"},
            },
        },
        handler=_handle_vault_retrieve,
    ),
]
