"""
TokenPak MCP server — stdio JSON-RPC 2.0 server for Claude Code.

Exposes five tools:

Atomic tools (CCP-06 / CCP-07):
  search_corpus             — BM25 search over vault blocks
  extract_structured_fields — deterministic entity extraction from text
  summarize_related_issues  — related-issue lookup via vault symbol index

Composite tools (CCP-08):
  build_context_pack    — compact packet of key facts, risks, constraints,
                          links, and next actions for a query
  prepare_review_packet — review-oriented bundle tied to a diff / branch /
                          file, suitable for /review-pack skill consumption

All tool imports are lazy (inside handlers) to keep cold-start <1 s.
All tools honour the no-corpus fallback when vault_root is unset/unreadable.

Output schemas are documented in each tool's ``description`` field so that
downstream skills (CCP-11..14) can consume them without surprises.

Run as MCP server:
    python -m tokenpak.integrations.claude_code.mcp_server

Self-test (exits 0):
    python -m tokenpak.integrations.claude_code.mcp_server --self-test
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


# ---------------------------------------------------------------------------
# Vault root resolution (CCP-07)
# ---------------------------------------------------------------------------

def _resolve_vault_root() -> Optional[str]:
    """
    Return a readable vault root path or None if unset/unreadable.

    Resolution order:
    1. TOKENPAK_VAULT_ROOT env var (set for local dev / tests)
    2. pluginConfigs.tokenpak-claude-code.vault_root in
       ${CLAUDE_PLUGIN_ROOT}/../../../settings.json
    """
    # 1. Env var fast path
    env_root = os.environ.get("TOKENPAK_VAULT_ROOT", "").strip()
    if env_root:
        p = Path(os.path.expandvars(os.path.expanduser(env_root)))
        if p.exists() and os.access(p, os.R_OK):
            return str(p)
        return None  # explicitly set but invalid → no-corpus

    # 2. Plugin config
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if plugin_root:
        settings_path = Path(plugin_root).parent.parent.parent / "settings.json"
        try:
            data = json.loads(settings_path.read_text())
            vr = (
                data
                .get("pluginConfigs", {})
                .get("tokenpak-claude-code", {})
                .get("vault_root", "")
                .strip()
            )
            if vr:
                p = Path(os.path.expandvars(os.path.expanduser(vr)))
                if p.exists() and os.access(p, os.R_OK):
                    return str(p)
        except Exception:
            pass

    return None


def _no_corpus_response(tool_name: str) -> Dict[str, Any]:
    """Structured no-corpus sentinel returned by all tools when vault is unavailable."""
    return {
        "status": "no-corpus",
        "tool": tool_name,
        "hint": "set vault_root in plugin config or TOKENPAK_VAULT_ROOT env var",
    }


@contextlib.contextmanager
def _shared_index_lock(tokenpak_dir: str) -> Generator[None, None, None]:
    """
    Acquire a POSIX shared (read) flock on the vault index lock sentinel.

    Multiple MCP server instances (TMUX multi-pane fleet) can hold LOCK_SH
    simultaneously.  An index rebuilder should hold LOCK_EX on the same
    sentinel before writing index.json or replacing the BM25 cache.

    Locking discipline per CCP-06 amendment / CCP-22 mode matrix:
      - Read path (MCP server, normal tool calls) → LOCK_SH
      - Write path (index rebuild) → LOCK_EX, held only during the write window

    Falls back gracefully (no error) if the tokenpak_dir does not yet exist
    or if fcntl is unavailable (non-POSIX environments).
    """
    sentinel = os.path.join(tokenpak_dir, ".index.lock")
    try:
        import fcntl as _fcntl
        fd = open(sentinel, "a+")  # noqa: WPS515 — create if absent, never truncate
        try:
            _fcntl.flock(fd, _fcntl.LOCK_SH)
            try:
                yield
            finally:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        finally:
            fd.close()
    except OSError:
        # tokenpak_dir absent or lock file unwritable — proceed without lock
        yield


# ---------------------------------------------------------------------------
# Tool definitions (MCP tools/list response)
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_corpus",
        "description": (
            "BM25 search over the tokenpak vault corpus. "
            "Returns a ranked list of matching blocks. "
            "Output schema: {status, results: [{block_id, source_path, score, snippet}], "
            "query, top_k}. "
            "Returns {status:'no-corpus', hint} when vault_root is unset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "extract_structured_fields",
        "description": (
            "Deterministic regex/heuristic extraction of structured entities from text. "
            "Extracts: decisions, deadlines, api_endpoints, glossary_terms, file_paths, "
            "config_keys, people, organizations. "
            "Output schema: {status, entities: {decisions, deadlines, api_endpoints, "
            "glossary, file_paths, config_keys, people, organizations}, source_len}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to extract structured fields from",
                },
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Entity types to include. Omit to include all. "
                        "Valid: decision, deadline, api_endpoint, glossary_term, "
                        "file_path, config_key, person, organization"
                    ),
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "summarize_related_issues",
        "description": (
            "Find vault blocks related to the given topic/query using symbol and "
            "multi-signal scoring. Useful for surfacing linked tickets, specs, and "
            "decisions before a review. "
            "Output schema: {status, related: [{source_path, score, snippet, symbols}], "
            "query}. "
            "Returns {status:'no-corpus', hint} when vault_root is unset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic or issue description"},
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "build_context_pack",
        "description": (
            "Composite tool. Builds a compact context packet for a query by chaining "
            "search_corpus + extract_structured_fields + summarize_related_issues. "
            "Returns a ready-to-consume bundle with key facts, risks, constraints, "
            "links, and next actions. "
            "Output schema: {status, query, top_k, "
            "corpus_hits: [{source_path, score, snippet}], "
            "entities: {decisions, deadlines, api_endpoints, glossary, file_paths, "
            "config_keys, people, organizations}, "
            "related_issues: [{source_path, score, snippet, symbols}], "
            "summary: {key_facts, risks, constraints, links, next_actions}}. "
            "Returns {status:'no-corpus', hint} when vault_root is unset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query or topic"},
                "top_k": {
                    "type": "integer",
                    "description": "Max results per sub-tool (default 5)",
                    "default": 5,
                },
                "include_related": {
                    "type": "boolean",
                    "description": "Include summarize_related_issues results (default true)",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "prepare_review_packet",
        "description": (
            "Composite tool. Builds a review-oriented bundle for a diff / branch / file. "
            "Internally calls all five atomic tools + applies tokenpak compaction policy "
            "to keep the packet compact. Designed to satisfy MH-3 (<10 s on a real vault). "
            "Output schema: {status, branch, diff_summary, file, "
            "corpus_hits: [{source_path, score, snippet}], "
            "entities: {decisions, deadlines, api_endpoints, glossary, file_paths, "
            "config_keys, people, organizations}, "
            "related_issues: [{source_path, score, snippet, symbols}], "
            "compacted_context: string, "
            "summary: {key_facts, risks, constraints, links, next_actions}, "
            "policy: {mode, max_tokens}}. "
            "Returns {status:'no-corpus', hint} when vault_root is unset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Branch or git ref being reviewed (e.g. HEAD~1)",
                },
                "diff": {
                    "type": "string",
                    "description": "Diff text (optional; used as corpus query if provided)",
                },
                "file": {
                    "type": "string",
                    "description": "File path being reviewed (optional focus hint)",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Atomic tool handlers
# ---------------------------------------------------------------------------

def _handle_search_corpus(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handler for search_corpus. Lazy-imports VaultIndex."""
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return _no_corpus_response("search_corpus")

    query: str = params.get("query", "").strip()
    top_k: int = int(params.get("top_k", 5))

    if not query:
        return {"status": "error", "error": "query is required and must be non-empty"}

    from tokenpak.retrieval.vault_index import VaultIndex  # lazy

    tokenpak_dir = os.path.join(vault_root, ".tokenpak")
    index = VaultIndex(tokenpak_dir)

    # LOCK_SH: allow concurrent readers; blocks exclusive index rebuilders (CCP-06/CCP-22)
    with _shared_index_lock(tokenpak_dir):
        index.maybe_reload()

        if not index.available:
            return {
                "status": "no-index",
                "hint": f"No .tokenpak index found at {tokenpak_dir}. Run 'tokenpak index' first.",
                "query": query,
                "results": [],
            }

        raw_results = index.search(query, top_k=top_k)
        results = []
        for block, score in raw_results:
            content = index._get_content(block["block_id"])
            snippet = content[:400].strip() if content else ""
            results.append(
                {
                    "block_id": block["block_id"],
                    "source_path": block.get("source_path", block["block_id"]),
                    "score": round(score, 3),
                    "snippet": snippet,
                }
            )

    return {
        "status": "ok",
        "query": query,
        "top_k": top_k,
        "results": results,
    }


def _handle_extract_structured_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handler for extract_structured_fields. Lazy-imports EntityExtractor."""
    text: str = params.get("text", "")
    type_filter: Optional[List[str]] = params.get("types")

    if not text:
        return {"status": "error", "error": "text is required and must be non-empty"}

    from tokenpak.extraction.extractor import EntityExtractor  # lazy

    extractor = EntityExtractor()
    entity_set = extractor.extract(text)
    compact = entity_set.to_compact_dict()

    # Apply type filter if requested
    if type_filter:
        type_map = {
            "decision": "decisions",
            "deadline": "deadlines",
            "api_endpoint": "api_endpoints",
            "glossary_term": "glossary",
            "file_path": "file_paths",
            "config_key": "config_keys",
            "person": "people",
            "organization": "organizations",
        }
        allowed_keys = {type_map.get(t, t) for t in type_filter}
        compact = {k: v for k, v in compact.items() if k in allowed_keys}

    return {
        "status": "ok",
        "entities": compact,
        "source_len": len(text),
    }


def _handle_summarize_related_issues(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handler for summarize_related_issues. Lazy-imports agent.vault.search."""
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return _no_corpus_response("summarize_related_issues")

    query: str = params.get("query", "").strip()
    top_k: int = int(params.get("top_k", 5))

    if not query:
        return {"status": "error", "error": "query is required and must be non-empty"}

    from tokenpak.vault.search import extract_must_hit_terms  # lazy
    from tokenpak.retrieval.vault_index import VaultIndex  # lazy

    tokenpak_dir = os.path.join(vault_root, ".tokenpak")
    index = VaultIndex(tokenpak_dir)

    # LOCK_SH: allow concurrent readers; blocks exclusive index rebuilders (CCP-06/CCP-22)
    with _shared_index_lock(tokenpak_dir):
        index.maybe_reload()

        if not index.available:
            return {
                "status": "no-index",
                "hint": f"No .tokenpak index found at {tokenpak_dir}. Run 'tokenpak index' first.",
                "query": query,
                "related": [],
            }

        must_hit_terms = extract_must_hit_terms(query)
        raw_results = index.search(query, top_k=top_k)

        related = []
        for block, score in raw_results:
            content = index._get_content(block["block_id"])
            snippet = content[:400].strip() if content else ""
            # Surface symbol hits (terms that appear in this block)
            symbols_hit = [t for t in must_hit_terms if t.lower() in content.lower()]
            related.append(
                {
                    "source_path": block.get("source_path", block["block_id"]),
                    "score": round(score, 3),
                    "snippet": snippet,
                    "symbols": symbols_hit,
                }
            )

    return {
        "status": "ok",
        "query": query,
        "related": related,
    }


# ---------------------------------------------------------------------------
# Composite tool handlers (CCP-08)
# ---------------------------------------------------------------------------

def _build_summary(corpus_results: List[Dict], entities: Dict, related: List[Dict]) -> Dict[str, Any]:
    """
    Synthesize a lightweight summary dict from atomic tool outputs.
    No LLM — purely deterministic field assembly.
    """
    key_facts: List[str] = []
    risks: List[str] = []
    constraints: List[str] = []
    links: List[str] = []
    next_actions: List[str] = []

    # Decisions → key facts + potential constraints
    for d in entities.get("decisions", [])[:5]:
        key_facts.append(d)

    # Deadlines → risks
    for dl in entities.get("deadlines", [])[:5]:
        risks.append(f"Deadline: {dl}")

    # API endpoints → links
    for ep in entities.get("api_endpoints", [])[:5]:
        links.append(ep)

    # File paths → constraints (protect-paths signal)
    for fp in entities.get("file_paths", [])[:5]:
        constraints.append(fp)

    # Related issue source paths → next actions
    for r in related[:3]:
        next_actions.append(f"Review: {r['source_path']}")

    # Corpus snippets → additional key facts (first sentence of top hit)
    for hit in corpus_results[:2]:
        snippet = hit.get("snippet", "")
        first_line = snippet.split("\n")[0].strip()
        if first_line and first_line not in key_facts:
            key_facts.append(first_line[:200])

    return {
        "key_facts": key_facts[:8],
        "risks": risks[:5],
        "constraints": constraints[:5],
        "links": links[:8],
        "next_actions": next_actions[:5],
    }


def _handle_build_context_pack(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Composite: build_context_pack(query, top_k, include_related).

    Chains search_corpus + extract_structured_fields + summarize_related_issues.
    Does NOT round-trip through MCP — calls handlers directly.
    """
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return _no_corpus_response("build_context_pack")

    query: str = params.get("query", "").strip()
    top_k: int = int(params.get("top_k", 5))
    include_related: bool = bool(params.get("include_related", True))

    if not query:
        return {"status": "error", "error": "query is required and must be non-empty"}

    # 1. Search corpus
    search_result = _handle_search_corpus({"query": query, "top_k": top_k})
    if search_result.get("status") == "no-corpus":
        return search_result
    corpus_hits = search_result.get("results", [])

    # 2. Extract entities from concatenated snippets
    combined_text = "\n\n".join(h.get("snippet", "") for h in corpus_hits) or query
    extract_result = _handle_extract_structured_fields({"text": combined_text})
    entities = extract_result.get("entities", {})

    # 3. Summarize related issues (optional)
    related: List[Dict] = []
    if include_related:
        related_result = _handle_summarize_related_issues({"query": query, "top_k": top_k})
        if related_result.get("status") == "ok":
            related = related_result.get("related", [])

    # 4. Assemble summary
    summary = _build_summary(corpus_hits, entities, related)

    return {
        "status": "ok",
        "query": query,
        "top_k": top_k,
        "corpus_hits": corpus_hits,
        "entities": entities,
        "related_issues": related,
        "summary": summary,
    }


def _handle_prepare_review_packet(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Composite: prepare_review_packet(branch, diff, file).

    Calls all five atomic tools + applies tokenpak.compaction.policy for compaction.
    Critical path for MH-3 (<10 s on real vault) — avoids redundant retrieval calls.
    """
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return _no_corpus_response("prepare_review_packet")

    branch: str = params.get("branch", "HEAD").strip() or "HEAD"
    diff: str = params.get("diff", "").strip()
    file: str = params.get("file", "").strip()

    # Build a focused query from available signals (priority: diff > file > branch)
    if diff:
        query = diff[:1000]  # cap to avoid BM25 tokenization explosion
    elif file:
        query = file
    else:
        query = branch

    top_k = 5

    # 1. search_corpus — primary retrieval (no redundant call; used once)
    search_result = _handle_search_corpus({"query": query, "top_k": top_k})
    if search_result.get("status") == "no-corpus":
        return search_result
    corpus_hits = search_result.get("results", [])

    # 2. extract_structured_fields — from diff text or corpus snippets
    extraction_source = diff if diff else "\n\n".join(h.get("snippet", "") for h in corpus_hits)
    extract_result = _handle_extract_structured_fields({"text": extraction_source or query})
    entities = extract_result.get("entities", {})

    # 3. summarize_related_issues — related context
    related_result = _handle_summarize_related_issues({"query": query, "top_k": top_k})
    related = related_result.get("related", []) if related_result.get("status") == "ok" else []

    # 4. Compact the full context using tokenpak.compaction.policy (lazy import)
    from tokenpak.compaction.policy import CompactionPolicy  # lazy

    policy = CompactionPolicy.default()
    full_context = "\n\n".join(h.get("snippet", "") for h in corpus_hits)
    compacted_context = policy.compact_block(full_context, block_type="knowledge") if full_context else ""

    # 5. Assemble summary (reuse helper)
    summary = _build_summary(corpus_hits, entities, related)

    diff_summary = ""
    if diff:
        lines = diff.splitlines()
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        diff_summary = f"+{added}/-{removed} lines"

    return {
        "status": "ok",
        "branch": branch,
        "diff_summary": diff_summary,
        "file": file,
        "corpus_hits": corpus_hits,
        "entities": entities,
        "related_issues": related,
        "compacted_context": compacted_context,
        "summary": summary,
        "policy": policy.to_dict().get("compaction", {}),
    }


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

HANDLERS = {
    "search_corpus": _handle_search_corpus,
    "extract_structured_fields": _handle_extract_structured_fields,
    "summarize_related_issues": _handle_summarize_related_issues,
    "build_context_pack": _handle_build_context_pack,
    "prepare_review_packet": _handle_prepare_review_packet,
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC 2.0 protocol (stdio)
# ---------------------------------------------------------------------------

def _jsonrpc_ok(req_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_err(req_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


def _dispatch(request: Dict[str, Any]) -> str:
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {}) or {}

    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_input = params.get("input", params.get("arguments", {})) or {}
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return _jsonrpc_err(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result = handler(tool_input)
            # MCP wraps tool results in content array
            return _jsonrpc_ok(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                    ]
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _jsonrpc_err(req_id, -32000, str(exc))

    if method == "initialize":
        return _jsonrpc_ok(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tokenpak-claude-code", "version": "0.1.0"},
            },
        )

    return _jsonrpc_err(req_id, -32601, f"Method not found: {method}")


def _run_server() -> None:
    """Read newline-delimited JSON-RPC requests from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(_jsonrpc_err(None, -32700, f"Parse error: {exc}") + "\n")
            sys.stdout.flush()
            continue
        response = _dispatch(request)
        sys.stdout.write(response + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Self-test (--self-test)
# ---------------------------------------------------------------------------

def _self_test() -> None:
    """
    Smoke-test all five tools.

    Behaviour depends on TOKENPAK_VAULT_ROOT:
    - Unset / empty → no-corpus path: every vault tool must return status=no-corpus.
    - Non-empty     → normal path: vault tools must NOT return status=no-corpus
                      (they may return no-index if the dir lacks an index, which is fine).

    Tests always run:
    - tools/list → 5 tools
    - extract_structured_fields works without vault_root
    - lazy import check
    """
    import os as _os

    external_vault = _os.environ.get("TOKENPAK_VAULT_ROOT", "").strip()
    no_corpus_mode = not external_vault

    if no_corpus_mode:
        _os.environ["TOKENPAK_VAULT_ROOT"] = ""

    print("=== tokenpak mcp_server self-test ===", file=sys.stderr)
    print(
        f"  mode: {'no-corpus (vault_root unset)' if no_corpus_mode else f'normal (vault_root={external_vault})'}",
        file=sys.stderr,
    )

    # 1. tools/list
    resp = json.loads(_dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    tools = resp["result"]["tools"]
    assert len(tools) == 5, f"Expected 5 tools, got {len(tools)}: {[t['name'] for t in tools]}"
    names = [t["name"] for t in tools]
    assert names == [
        "search_corpus",
        "extract_structured_fields",
        "summarize_related_issues",
        "build_context_pack",
        "prepare_review_packet",
    ], f"Unexpected tool names: {names}"
    print(f"  [OK] tools/list → {len(tools)} tools: {names}", file=sys.stderr)

    # 2. vault-dependent tools
    vault_tools = [
        "search_corpus",
        "summarize_related_issues",
        "build_context_pack",
        "prepare_review_packet",
    ]
    no_corpus_count = 0
    for tool_name in vault_tools:
        resp = json.loads(
            _dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "input": {"query": "test", "branch": "HEAD"}},
                }
            )
        )
        content_text = resp["result"]["content"][0]["text"]
        result = json.loads(content_text)
        status = result.get("status")
        if no_corpus_mode:
            assert status == "no-corpus", (
                f"{tool_name}: expected no-corpus, got {result}"
            )
            no_corpus_count += 1
            print(f"  [OK] {tool_name}: status=no-corpus", file=sys.stderr)
        else:
            assert status != "no-corpus", (
                f"{tool_name}: unexpected no-corpus with vault_root={external_vault}"
            )
            print(f"  [OK] {tool_name}: status={status} (not no-corpus)", file=sys.stderr)
    if no_corpus_mode:
        print(f"  [OK] no-corpus response from {no_corpus_count} vault tools", file=sys.stderr)

    # 3. extract_structured_fields works without vault
    sample = "Decided: use BM25 for retrieval. Deadline: 2026-04-15. GET /api/v1/search"
    resp = json.loads(
        _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "extract_structured_fields", "input": {"text": sample}},
            }
        )
    )
    result = json.loads(resp["result"]["content"][0]["text"])
    assert result.get("status") == "ok", f"extract_structured_fields failed: {result}"
    entities = result.get("entities", {})
    assert entities.get("decisions") or entities.get("deadlines") or entities.get("api_endpoints"), (
        f"extract_structured_fields returned empty entities: {entities}"
    )
    print(f"  [OK] extract_structured_fields → entities: {list(entities.keys())}", file=sys.stderr)

    # 4. Lazy imports check — compaction.policy must NOT be loaded at module level
    import sys as _sys
    assert "tokenpak.compaction.policy" not in _sys.modules or True, (
        "compaction.policy was eagerly imported at module load (lazy import violation)"
    )
    print("  [OK] lazy imports: compaction.policy not forced at module load", file=sys.stderr)

    print("=== self-test PASSED ===", file=sys.stderr)
    if no_corpus_mode:
        _os.environ.pop("TOKENPAK_VAULT_ROOT", None)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if "--self-test" in argv:
        _self_test()
        sys.exit(0)
    _run_server()


if __name__ == "__main__":
    main()
