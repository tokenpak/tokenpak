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

from tokenpak.companion import config as _companion_config

from ..config import CompanionConfig

# Default input-rate for cost estimation when no model hint is available.
# Matches pre_send.py's fallback (sonnet input rate). Kept local rather than
# imported so tools.py has no cross-module coupling with the hook.
_COMPANION_DEFAULT_INPUT_RATE_USD_PER_MTOK = 3.0


def current_session_id() -> str:
    """Read the live session id from the run-dir marker written by the
    pre_send hook (session binding). The MCP server is a separate process
    from the hook, so this file is the only channel by which it learns the
    active session id. Returns "" if no marker exists yet."""
    try:
        run_dir = _companion_config.journal_run_dir()
        marker = run_dir / "current-session"
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


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
    """MCP tool definition.

    ``core`` marks tools advertised under the ``lean`` companion profile.
    Tool schemas are re-sent to the model with every request, so the lean
    profile advertises only the tools whose value justifies that recurring
    cost; hooks and the CLI cover the rest out-of-band. Dispatch is never
    filtered — a call to an unadvertised tool still works.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[CompanionState, dict[str, Any]], str]
    core: bool = True


def _handle_estimate_tokens(state: CompanionState, args: dict[str, Any]) -> str:
    """Estimate tokens via proxy /tpk/v1/tokens/estimate."""
    text = args.get("text", "")
    file_path = args.get("file_path", "")
    body: dict[str, Any] = {}
    if file_path:
        body["file_path"] = file_path
    elif text:
        body["text"] = text
    else:
        return json.dumps({"error": "provide text or file_path"})

    status, resp = _proxy_post("/tpk/v1/tokens/estimate", body)
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": resp.get("detail", "")})
    if status >= 400:
        return json.dumps(resp)
    # Tool results persist in the conversation and are re-sent as input on
    # every later turn, so return only the fields an agent acts on. The
    # estimator disclosure stays (a heuristic count must not read as a
    # tokenizer count) but in compact form; the full response remains
    # available on the HTTP endpoint itself.
    compact: dict[str, Any] = {k: resp[k] for k in ("tokens", "chars") if k in resp}
    estimator = resp.get("estimator")
    if estimator == "chars-per-4-heuristic":
        compact["estimator"] = "chars/4-approx"
        compact["note"] = "install tokenpak[tokens] for tokenizer counts"
    elif estimator:
        compact["estimator"] = estimator
    return json.dumps(compact)


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
            tokens = sum(count_tokens(text[i : i + CHUNK]) for i in range(0, chars, CHUNK))
        method = "tiktoken"
    except Exception:
        tokens = chars // 4
        method = "heuristic (chars/4)"

    return json.dumps(
        {
            "tokens": tokens,
            "chars": chars,
            "method": method,
            "source": file_path or "inline text",
        },
        indent=2,
    )


def _handle_session_economics(state: CompanionState, args: dict[str, Any]) -> str:
    """Read-only deterministic trip-computer state via the proxy endpoint.

    Session selection: an explicit ``session_id`` argument wins; otherwise
    the state's bound session (refreshed from the active-session marker
    before each dispatch) is used; otherwise an empty id lets the proxy
    fall back to the latest completed ledger session. The response is
    validated against the shared contract before being returned, and the
    returned string is the canonical contract JSON — identical values to
    the status and dashboard surfaces by construction. This tool never
    calls a provider and never writes anything.
    """
    session_id = str(args.get("session_id", "") or "").strip()
    if not session_id:
        session_id = state.session_id or current_session_id()
    body: dict[str, Any] = {}
    if session_id:
        body["session_id"] = session_id
    status, resp = _proxy_post("/v1/messages/session-economics", body)
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": resp.get("detail", "")})
    if status >= 400:
        return json.dumps(resp)
    try:
        from tokenpak.core.contracts.session_economics import SessionEconomics

        return SessionEconomics.from_dict(resp).to_json()
    except Exception as exc:
        return json.dumps({"error": "invalid_session_economics_payload", "detail": str(exc)})


def _handle_check_budget(state: CompanionState, args: dict[str, Any]) -> str:
    """Check remaining budget via proxy /tpk/v1/budget."""
    status, body = _proxy_get("/tpk/v1/budget")
    if status == 0:
        return json.dumps(
            {
                "error": "proxy_unreachable",
                "detail": body.get("detail", "is the tokenpak proxy running?"),
            }
        )
    if status >= 400:
        return json.dumps(body)
    # Honest-reporting (split-brain trust fix): the proxy only accounts for
    # traffic actually routed through the TokenPak value plane. A client routed
    # natively (e.g. provider-native caching, no TokenPak proxy in path) is NOT
    # counted here — so a low/zero figure must never be read as authoritative
    # total spend. Always attach an explicit scope note; never return a bare 0.
    if isinstance(body, dict):
        body = dict(body)
        body["_tokenpak_scope"] = (
            "Reflects ONLY traffic routed through the TokenPak value plane "
            "(proxy/companion). Natively-routed client traffic is NOT counted "
            "here; a low or zero figure does not mean low total spend. If this "
            "client is not routed through TokenPak, treat TokenPak accounting "
            "as unavailable for it."
        )
    return json.dumps(body, indent=2)


def _handle_load_capsule(state: CompanionState, args: dict[str, Any]) -> str:
    """Load / list memory capsules via proxy /tpk/v1/capsules*."""
    session_id = str(args.get("session_id", "")).strip()
    raw_session_ids = args.get("session_ids")
    raw_include_journal = args.get("include_journal", False)
    if not isinstance(raw_include_journal, bool):
        return json.dumps({"error": "include_journal must be a boolean"})
    include_journal = raw_include_journal

    # Preserve the legacy single-session and list responses byte-for-byte.
    if raw_session_ids is None and not include_journal:
        if not session_id:
            status, body = _proxy_get("/tpk/v1/capsules", {"limit": 10})
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
            f"/tpk/v1/capsules/{_url_parse.quote(session_id, safe='')}",
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

    if raw_session_ids is not None and not isinstance(raw_session_ids, list):
        return json.dumps({"error": "session_ids must be an array of strings"})
    if raw_session_ids is not None and not all(
        isinstance(value, str) and value.strip() for value in raw_session_ids
    ):
        return json.dumps({"error": "session_ids must contain non-empty strings"})
    if raw_session_ids is not None and len(raw_session_ids) > 10:
        return json.dumps({"error": "session_ids supports at most 10 sessions"})

    requested_ids = [session_id] if session_id else []
    for value in raw_session_ids or []:
        candidate = value.strip()
        if candidate and candidate not in requested_ids:
            requested_ids.append(candidate)
    if not requested_ids:
        return json.dumps({"error": "provide session_id or session_ids for batched retrieval"})
    if len(requested_ids) > 10:
        return json.dumps({"error": "session_ids supports at most 10 sessions"})

    sections: list[str] = []
    if include_journal:
        for target in requested_ids:
            status, body = _proxy_get(
                f"/tpk/v1/journal/{_url_parse.quote(target, safe='')}",
                {"limit": 20},
            )
            if status == 0:
                body = {"error": "proxy_unreachable", "detail": body.get("detail", "")}
            sections.append(
                f"[Journal: {target}]\n"
                + json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            )

    caller_params = {"caller_session_id": state.session_id} if state.session_id else {}
    for target in requested_ids:
        status, body = _proxy_get(
            f"/tpk/v1/capsules/{_url_parse.quote(target, safe='')}",
            caller_params,
        )
        if status == 0:
            body = {"error": "proxy_unreachable", "detail": body.get("detail", "")}
        if status < 400 and isinstance(body, dict) and "content" in body:
            content = str(body["content"])
        else:
            content = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        sections.append(f"[Pak: {target}]\n{content}")
    return "\n\n".join(sections)


def _handle_prune_context(state: CompanionState, args: dict[str, Any]) -> str:
    """Compress verbose content via proxy /tpk/v1/compress."""
    text = args.get("text", "")
    if not text:
        return json.dumps({"error": "No text provided"})
    body = {
        "text": text,
        "max_tokens": args.get("max_tokens", 2000),
    }
    if state.session_id:
        body["session_id"] = state.session_id  # proxy records savings to journal
    status, resp = _proxy_post("/tpk/v1/compress", body)
    if status == 0:
        return json.dumps({"error": "proxy_unreachable", "detail": resp.get("detail", "")})
    if status >= 400:
        return json.dumps(resp)
    # Wrap actual reductions in the Pak envelope so compressed content that
    # re-enters model context is attributable to TokenPak.
    if isinstance(resp, dict) and resp.get("pruned_text") and resp.get("reduction_pct", 0) > 0:
        from ..capsules.builder import _wrap_capsule

        resp = dict(resp)
        resp["pruned_text"] = _wrap_capsule(text, resp["pruned_text"])
    return json.dumps(resp)


def _handle_journal_read(state: CompanionState, args: dict[str, Any]) -> str:
    """Read journal entries via proxy /tpk/v1/journal/*."""
    target = args.get("session_id") or state.session_id
    entry_type = args.get("entry_type")
    limit = args.get("limit", 20)

    if not target:
        # List recent sessions
        status, body = _proxy_get("/tpk/v1/journal/sessions", {"limit": 10})
    else:
        params: dict[str, Any] = {"limit": limit}
        if entry_type:
            params["entry_type"] = entry_type
        status, body = _proxy_get(
            f"/tpk/v1/journal/{_url_parse.quote(target, safe='')}",
            params,
        )

    if status == 0:
        return json.dumps(
            {
                "error": "proxy_unreachable",
                "detail": body.get("detail", ""),
            }
        )
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
        f"/tpk/v1/journal/{_url_parse.quote(session_id, safe='')}/entry",
        {"content": content, "entry_type": "user"},
    )
    if status == 0:
        return json.dumps(
            {
                "error": "proxy_unreachable",
                "detail": body.get("detail", ""),
            }
        )
    if status >= 400:
        return json.dumps(body)
    return json.dumps(body)


def _handle_session_info(state: CompanionState, args: dict[str, Any]) -> str:
    """Environment snapshot: merges local companion state with proxy status.

    Companion-local fields (session_id, call_count, config) stay in-process
    since they describe the companion's own process. Proxy-owned state
    (version, uptime, mode, cache_ttl, vault, session counters) comes from
    /tpk/v1/session/info. Proxy-down returns the local-only view with a
    "proxy" block indicating the degradation.
    """
    local = {
        "companion_version": "0.1.0",
        "session_id": state.session_id,
        "call_count": state.call_count,
        "config": {
            "profile": state.config.profile,
            "budget_daily_usd": state.config.budget_daily_usd,
            "hooks_enabled": state.config.hooks_enabled,
            "prune_threshold": state.config.prune_threshold,
            "memory_dirs": [str(p) for p in getattr(state.config, "memory_dirs", [])],
        },
    }
    # Make "no lessons" self-explaining: report whether a memory source is
    # configured at all (TOKENPAK_COMPANION_MEMORY_DIRS / vault), so a
    # fresh user knows why ingestion may be empty and how to point it at notes.
    if not local["config"]["memory_dirs"]:
        local["config"]["memory_source_hint"] = (
            "no memory dirs configured — set TOKENPAK_COMPANION_MEMORY_DIRS to "
            "directories of your own Markdown notes, then ingest them with the "
            "companion memory-source API (ingest_from_dir / ingest_sources)"
        )
    status, proxy_info = _proxy_get("/tpk/v1/session/info")
    if status == 200:
        local["proxy"] = proxy_info
    elif status == 0:
        local["proxy"] = {"error": "proxy_unreachable", "detail": proxy_info.get("detail", "")}
    else:
        local["proxy"] = proxy_info
    return json.dumps(local, indent=2)


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


def _proxy_request(
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    """HTTP call against the local proxy's /tpk/v1/* app API.

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
        req.add_header("X-TPK-Key", key)
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


def _proxy_post(
    path: str, body: Optional[dict[str, Any]] = None, params: Optional[dict[str, Any]] = None
) -> tuple[int, dict[str, Any]]:
    return _proxy_request("POST", path, params=params, body=body)


# ---------------------------------------------------------------------------
# Vault access — exposes V1/V4/V6/V8 Free features as MCP tools.
# Thin HTTP wrappers over the proxy's /tpk/v1/vault/* endpoints so the
# companion does NOT hold its own VaultIndex instance.
# ---------------------------------------------------------------------------


def _handle_vault_search(state: CompanionState, args: dict[str, Any]) -> str:
    """Search the vault via the proxy's /tpk/v1/vault/search endpoint."""
    query = str(args.get("query", "")).strip()
    if not query:
        return json.dumps({"error": "query is required"})
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))

    status, body = _proxy_get("/tpk/v1/vault/search", {"q": query, "limit": limit})
    if status == 0:
        return json.dumps(
            {
                "error": "proxy_unreachable",
                "detail": body.get("detail", "is the tokenpak proxy running? try `tokenpak start`"),
            }
        )
    if status >= 400:
        return json.dumps(body)
    # Pass through the proxy's response shape as-is; it already matches our contract.
    return json.dumps(body, indent=2)


def _handle_vault_retrieve(state: CompanionState, args: dict[str, Any]) -> str:
    """Fetch a vault block via the proxy's /tpk/v1/vault/block/{id} endpoint."""
    block_id = str(args.get("block_id", "")).strip()
    path_hint = str(args.get("path", "")).strip()
    if not block_id and not path_hint:
        return json.dumps({"error": "provide block_id or path"})

    # If only a path hint is given, resolve via search first to get an exact id.
    if not block_id and path_hint:
        status, body = _proxy_get("/tpk/v1/vault/search", {"q": path_hint, "limit": 1})
        if status == 0:
            return json.dumps({"error": "proxy_unreachable", "detail": body.get("detail", "")})
        results = body.get("results") or []
        if not results:
            return json.dumps({"error": "block_not_found", "path": path_hint})
        block_id = results[0].get("block_id") or ""

    status, body = _proxy_get(f"/tpk/v1/vault/block/{_url_parse.quote(block_id, safe='')}")
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
        description="Estimate token count for text or a file. Cost tracking is automatic via hooks — reserve this for a go/no-go decision on very large content, not routine bookkeeping.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to estimate tokens for"},
                "file_path": {
                    "type": "string",
                    "description": "Path to a file (alternative to text)",
                },
            },
        },
        handler=_handle_estimate_tokens,
        core=False,
    ),
    ToolDef(
        name="session_economics",
        description="Read the deterministic session trip-computer: spent tokens/cost, burn, binding runway, guard state, and forecast availability. Read-only; facts and explicit unknowns only — no recommendations.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session to read (omit for the active/most recent session)",
                },
            },
        },
        handler=_handle_session_economics,
        core=False,
    ),
    ToolDef(
        name="check_budget",
        description="Report the remaining TokenPak cost budget for this session and today. The pre-send hook enforces the budget automatically — call this only when the user asks about budget.",
        input_schema={"type": "object", "properties": {}},
        handler=_handle_check_budget,
        core=False,
    ),
    ToolDef(
        name="load_pak",
        description="Load prior work from one or more Paks. Pass session_ids and include_journal to retrieve what you need together; omit IDs to list available Paks.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to load (omit to list available)",
                },
                "session_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                    "description": "Session IDs to load together",
                },
                "include_journal": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include each requested session's journal digest",
                },
            },
        },
        handler=_handle_load_capsule,
    ),
    ToolDef(
        name="load_capsule",
        description="Deprecated legacy alias of load_pak (capsule is the pre-rebrand name for a Pak). Prefer load_pak.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to load (omit to list available)",
                },
                "session_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                    "description": "Session IDs to load together",
                },
                "include_journal": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include each requested session's journal digest",
                },
            },
        },
        handler=_handle_load_capsule,
        core=False,
    ),
    ToolDef(
        name="prune_context",
        description="Compress verbose text (large tool outputs, error logs) with TokenPak to reduce token usage. Keeps the beginning and end, elides the middle.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to prune"},
                "max_tokens": {
                    "type": "integer",
                    "description": "Target token count (default 2000)",
                    "default": 2000,
                },
            },
            "required": ["text"],
        },
        handler=_handle_prune_context,
    ),
    ToolDef(
        name="journal_read",
        description="Read TokenPak session-journal entries for this session or a past session. Omit session_id to list recent sessions.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session to query (default: current)",
                },
                "entry_type": {
                    "type": "string",
                    "description": "Filter by type: auto, user, milestone, cost",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 20)",
                    "default": 20,
                },
            },
        },
        handler=_handle_journal_read,
    ),
    ToolDef(
        name="journal_write",
        description="Add a note to the TokenPak session journal. Use for important decisions, milestones, or context the user might want later.",
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
        description="Get TokenPak companion status, session stats, and configuration.",
        input_schema={"type": "object", "properties": {}},
        handler=_handle_session_info,
        core=False,
    ),
    ToolDef(
        name="vault_search",
        description=(
            "Search the indexed vault by BM25; returns top-K matching blocks "
            "with relevance scores. Use when the user references docs, code, "
            "or knowledge stored in the local vault."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (words or phrase)"},
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5, max 20)",
                    "default": 5,
                },
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
                "path": {
                    "type": "string",
                    "description": "Path substring to match (alternative to block_id)",
                },
            },
        },
        handler=_handle_vault_retrieve,
    ),
]


def active_tools(profile: str) -> list[ToolDef]:
    """Tools advertised for a companion profile.

    ``lean`` advertises only core tools; every other profile advertises the
    full registry. Derived from per-tool ``core`` metadata, never a separate
    name list, so registry changes cannot drift out of sync.
    """
    if profile == "lean":
        return [t for t in TOOLS if t.core]
    return list(TOOLS)
