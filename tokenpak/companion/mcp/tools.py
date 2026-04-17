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
from typing import Any, Callable, Optional

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
    """Estimate tokens via proxy /tp/v1/tokens/estimate."""
    text = args.get("text", "")
    file_path = args.get("file_path", "")
    body: dict[str, Any] = {}
    if file_path:
        body["file_path"] = file_path
    elif text:
        body["text"] = text
    else:
        return json.dumps({"error": "provide text or file_path"})

    status, resp = _proxy_post("/tp/v1/tokens/estimate", body)
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": resp.get("detail", "")})
    if status >= 400:
        return json.dumps(resp)
    return json.dumps(resp)


def _handle_estimate_tokens_legacy_unused(state: CompanionState, args: dict[str, Any]) -> str:
    """Legacy in-process estimator kept for reference; no longer registered."""
    text = args.get("text", "")
    file_path = args.get("file_path", "")
    if file_path:
        p = Path(file_path)
        if p.exists():
            text = p.read_text(errors="replace")
        else:
            return json.dumps({"error": f"File not found: {file_path}"})

    chars = len(text)
    try:
        from tokenpak.telemetry.tokens import count_tokens
        CHUNK = 100_000
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
    """Check remaining budget via proxy /tp/v1/budget."""
    status, body = _proxy_get("/tp/v1/budget")
    if status == 0:
        return json.dumps({
            "error": "proxy_unreachable",
            "detail": body.get("detail", "is the tokenpak proxy running?"),
        })
    if status >= 400:
        return json.dumps(body)
    return json.dumps(body, indent=2)


def _handle_load_capsule(state: CompanionState, args: dict[str, Any]) -> str:
    """Load / list memory capsules via proxy /tp/v1/capsules*."""
    session_id = str(args.get("session_id", "")).strip()
    if not session_id:
        status, body = _proxy_get("/tp/v1/capsules", {"limit": 10})
        if status == 0:
            return json.dumps({"error": "proxy_unreachable", "detail": body.get("detail", "")})
        if status >= 400:
            return json.dumps(body)
        return json.dumps(body, indent=2)

    # Carry the CALLER's session_id so the proxy can attribute the
    # load_capsule savings event to the right session journal.
    params = {}
    if state.session_id:
        params["caller_session_id"] = state.session_id
    status, body = _proxy_get(
        f"/tp/v1/capsules/{_url_parse.quote(session_id, safe='')}",
        params,
    )
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": body.get("detail", "")})
    if status >= 400:
        return json.dumps(body)
    # Preserve the old behavior of returning the capsule CONTENT as a bare
    # string when a specific session was requested.
    if isinstance(body, dict) and "content" in body:
        return body["content"]
    return json.dumps(body)


def _handle_prune_context(state: CompanionState, args: dict[str, Any]) -> str:
    """Compress verbose content via proxy /tp/v1/compress."""
    text = args.get("text", "")
    if not text:
        return json.dumps({"error": "No text provided"})
    body = {
        "text": text,
        "max_tokens": args.get("max_tokens", 2000),
    }
    if state.session_id:
        body["session_id"] = state.session_id  # proxy records savings to journal
    status, resp = _proxy_post("/tp/v1/compress", body)
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": resp.get("detail", "")})
    if status >= 400:
        return json.dumps(resp)
    return json.dumps(resp)


def _handle_journal_read(state: CompanionState, args: dict[str, Any]) -> str:
    """Read journal entries via proxy /tp/v1/journal/*."""
    target = args.get("session_id") or state.session_id
    entry_type = args.get("entry_type")
    limit = args.get("limit", 20)

    if not target:
        # List recent sessions
        status, body = _proxy_get("/tp/v1/journal/sessions", {"limit": 10})
    else:
        params: dict[str, Any] = {"limit": limit}
        if entry_type:
            params["entry_type"] = entry_type
        status, body = _proxy_get(
            f"/tp/v1/journal/{_url_parse.quote(target, safe='')}",
            params,
        )

    if status == 0:
        return json.dumps({
            "error": "proxy_unreachable",
            "detail": body.get("detail", ""),
        })
    if status >= 400:
        return json.dumps(body)
    return json.dumps(body, indent=2)


def _handle_journal_write(state: CompanionState, args: dict[str, Any]) -> str:
    """Add a note to the current session journal via proxy POST."""
    content = args.get("content", "")
    if not content:
        return json.dumps({"error": "No content provided"})

    session_id = state.session_id
    if not session_id:
        return json.dumps({"error": "No active session"})

    status, body = _proxy_post(
        f"/tp/v1/journal/{_url_parse.quote(session_id, safe='')}/entry",
        {"content": content, "entry_type": "user"},
    )
    if status == 0:
        return json.dumps({
            "error": "proxy_unreachable",
            "detail": body.get("detail", ""),
        })
    if status >= 400:
        return json.dumps(body)
    return json.dumps(body)


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
# Proxy REST client — thin HTTP wrapper used by vault_* (and future) tools.
# Per 2026-04-17 architecture: proxy owns the state, companion calls it.
# ---------------------------------------------------------------------------

import os as _os
import urllib.error as _url_err
import urllib.parse as _url_parse
import urllib.request as _url_req


def _proxy_base_url() -> str:
    return _os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")


def _proxy_request(method: str, path: str, params: Optional[dict[str, Any]] = None, body: Optional[dict[str, Any]] = None) -> tuple[int, dict[str, Any]]:
    """HTTP call against the local proxy's /tp/v1/* app API.

    Returns (status_code, json_body). Never raises — network/parse errors
    become (0, {"error": ..., "detail": ...}) so tool handlers can
    degrade gracefully.
    """
    url = _proxy_base_url().rstrip("/") + path
    if params:
        url = f"{url}?{_url_parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    data: Optional[bytes] = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = _url_req.Request(url, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    key = _os.environ.get("TOKENPAK_PROXY_KEY", "").strip()
    if key:
        req.add_header("X-TP-Key", key)
    try:
        with _url_req.urlopen(req, timeout=5.0) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception as exc:
                return resp.status, {"error": "invalid_json", "detail": str(exc)}
    except _url_err.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except Exception:
            parsed = {"error": f"http_{exc.code}", "detail": str(exc)}
        return exc.code, parsed
    except Exception as exc:
        return 0, {"error": "proxy_unreachable", "detail": str(exc)}


def _proxy_get(path: str, params: Optional[dict[str, Any]] = None) -> tuple[int, dict[str, Any]]:
    return _proxy_request("GET", path, params=params)


def _proxy_post(path: str, body: Optional[dict[str, Any]] = None, params: Optional[dict[str, Any]] = None) -> tuple[int, dict[str, Any]]:
    return _proxy_request("POST", path, params=params, body=body)


# ---------------------------------------------------------------------------
# Vault access — exposes V1/V4/V6/V8 Free features as MCP tools.
# Thin HTTP wrappers over the proxy's /tp/v1/vault/* endpoints so the
# companion does NOT hold its own VaultIndex instance.
# ---------------------------------------------------------------------------


def _handle_vault_search(state: CompanionState, args: dict[str, Any]) -> str:
    """Search the vault via the proxy's /tp/v1/vault/search endpoint."""
    query = str(args.get("query", "")).strip()
    if not query:
        return json.dumps({"error": "query is required"})
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))

    status, body = _proxy_get("/tp/v1/vault/search", {"q": query, "limit": limit})
    if status == 0:
        return json.dumps({
            "error": "proxy_unreachable",
            "detail": body.get("detail", "is the tokenpak proxy running? try `tokenpak start`"),
        })
    if status >= 400:
        return json.dumps(body)
    # Pass through the proxy's response shape as-is; it already matches our contract.
    return json.dumps(body, indent=2)


def _handle_vault_retrieve(state: CompanionState, args: dict[str, Any]) -> str:
    """Fetch a vault block via the proxy's /tp/v1/vault/block/{id} endpoint."""
    block_id = str(args.get("block_id", "")).strip()
    path_hint = str(args.get("path", "")).strip()
    if not block_id and not path_hint:
        return json.dumps({"error": "provide block_id or path"})

    # If only a path hint is given, resolve via search first to get an exact id.
    if not block_id and path_hint:
        status, body = _proxy_get("/tp/v1/vault/search", {"q": path_hint, "limit": 1})
        if status == 0:
            return json.dumps({"error": "proxy_unreachable", "detail": body.get("detail", "")})
        results = body.get("results") or []
        if not results:
            return json.dumps({"error": "block_not_found", "path": path_hint})
        block_id = results[0].get("block_id") or ""

    status, body = _proxy_get(f"/tp/v1/vault/block/{_url_parse.quote(block_id, safe='')}")
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": body.get("detail", "")})
    if status >= 400:
        return json.dumps(body)
    return json.dumps(body, indent=2)


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
