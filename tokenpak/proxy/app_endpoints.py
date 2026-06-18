# SPDX-License-Identifier: Apache-2.0
"""App-level REST endpoints under ``/tpk/v1/*``.

These are the tokenpak-APP API routes — distinct from the ``/v1/*`` LLM
passthrough that Anthropic/OpenAI clients hit. The app API is what the
companion + external dashboards call to consume proxy-owned state
(vault index, budget tracker, journal, stats) without reaching into the
Python package directly.

Architectural contract (per Kevin's 2026-04-17 design call):
    - Proxy owns the heavy-lifting modules (VaultIndex, etc.)
    - Companion is a thin HTTP adapter — tool calls become requests here
    - No adapter reimplements what lives in the proxy

Authentication (localhost-only by default):
    - Requests must arrive from 127.0.0.1 / ::1 unless ``TOKENPAK_PROXY_KEY``
      is set, in which case an ``X-TPK-Key`` header must match.
    - No CORS / cross-origin story today; GTM is single-host dev use.

Error shape:
    {"error": "<code>", "detail": "<human message>"}
    Status codes match HTTP semantics (400 malformed, 401 unauthorized,
    404 not found, 500 internal, 503 index not loaded).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

from tokenpak.companion.recall import (
    LIST_LIMIT_DEFAULT,
    PakListFilters,
)

if TYPE_CHECKING:  # noqa: F401 — types only used in string-quoted hints
    from tokenpak.companion.recall import PakRow, RecallStore

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _is_authorized(handler: Any) -> bool:
    """Check localhost + optional X-TPK-Key header."""
    # Localhost gate — reject non-loopback by default
    client_ip = getattr(handler, "client_address", ("", 0))[0] or ""
    if client_ip not in ("127.0.0.1", "::1", "localhost", ""):
        return False

    key = os.environ.get("TOKENPAK_PROXY_KEY", "").strip()
    if not key:
        return True
    return handler.headers.get("X-TPK-Key", "") == key


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _send_json(handler: Any, status: int, payload: dict[str, Any]) -> None:
    # allow_nan=False → strict JSON; NaN/Infinity raise ValueError. Callers
    # must sanitize unbounded floats before passing to this helper.
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler: Any, status: int, code: str, detail: str = "") -> None:
    _send_json(handler, status, {"error": code, "detail": detail})


# ---------------------------------------------------------------------------
# Endpoint dispatch
# ---------------------------------------------------------------------------


def try_handle_get(handler: Any) -> bool:
    """If handler.path starts with /tpk/v1/ or /pak/v1/, handle it and return True.

    Return False to let the default dispatch continue.

    Two namespaces are handled here:

    - ``/tpk/v1/*`` — the OSS app API (vault, budget, journal, capsules,
      models). This is the canonical proxy-owned read surface per glossary
      ``api-tpk-v1``.
    - ``/pak/v1/*`` — the MultiPak Pro daemon protocol surface. Stubs return
      ``not_implemented`` when the Pro daemon is absent; ``status`` always
      works.
    """
    parsed = urlparse(handler.path)
    path = parsed.path
    if path.startswith("/pak/v1/"):
        return _try_handle_pak_get(handler, path, parsed)
    if not path.startswith("/tpk/v1/"):
        return False

    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized", "localhost-only; set X-TPK-Key if TOKENPAK_PROXY_KEY is configured")
        return True

    qs = parse_qs(parsed.query or "")

    # ── /tpk/v1/health ────────────────────────────────────────────────────
    if path == "/tpk/v1/health":
        _handle_health(handler)
        return True

    # ── /tpk/v1/vault/search?q=...&limit=N ───────────────────────────────
    if path == "/tpk/v1/vault/search":
        _handle_vault_search(handler, qs)
        return True

    # ── /tpk/v1/vault/block/{block_id} ───────────────────────────────────
    if path.startswith("/tpk/v1/vault/block/"):
        block_id = path[len("/tpk/v1/vault/block/"):]
        _handle_vault_block(handler, block_id)
        return True

    # ── /tpk/v1/budget ────────────────────────────────────────────────────
    if path == "/tpk/v1/budget":
        _handle_budget_get(handler, qs)
        return True

    # ── /tpk/v1/journal/sessions ─────────────────────────────────────────
    if path == "/tpk/v1/journal/sessions":
        _handle_journal_sessions(handler, qs)
        return True

    # ── /tpk/v1/journal/{session_id} ─────────────────────────────────────
    if path.startswith("/tpk/v1/journal/"):
        session_id = path[len("/tpk/v1/journal/"):]
        _handle_journal_get(handler, session_id, qs)
        return True

    # ── /tpk/v1/capsules ─ list available capsules ─────────────────────
    if path == "/tpk/v1/capsules":
        _handle_capsules_list(handler, qs)
        return True

    # ── /tpk/v1/capsules/{session_id} ─ load a specific capsule ────────
    if path.startswith("/tpk/v1/capsules/"):
        session_id = path[len("/tpk/v1/capsules/"):]
        _handle_capsule_get(handler, session_id, qs)
        return True

    # ── /tpk/v1/session/info ─ proxy-side environment snapshot ─────────
    if path == "/tpk/v1/session/info":
        _handle_session_info_get(handler)
        return True

    # ── /tpk/v1/models ─ known models from the registry ──────────────
    if path == "/tpk/v1/models":
        _handle_models_list(handler, qs)
        return True

    _send_error(handler, 404, "not_found", f"unknown app endpoint: {path}")
    return True


def try_handle_post(handler: Any) -> bool:
    """POST dispatch for app endpoints that accept a body."""
    parsed = urlparse(handler.path)
    path = parsed.path
    if path.startswith("/pak/v1/"):
        return _try_handle_pak_post(handler, path)
    if not path.startswith("/tpk/v1/"):
        return False

    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized")
        return True

    # ── POST /tpk/v1/journal/{session_id}/entry ──────────────────────────
    if path.startswith("/tpk/v1/journal/") and path.endswith("/entry"):
        session_id = path[len("/tpk/v1/journal/"):-len("/entry")]
        body = _read_json_body(handler)
        if body is None:
            _send_error(handler, 400, "invalid_json", "request body must be JSON")
            return True
        _handle_journal_post(handler, session_id, body)
        return True

    # ── POST /tpk/v1/compress ────────────────────────────────────────────
    if path == "/tpk/v1/compress":
        body = _read_json_body(handler)
        if body is None:
            _send_error(handler, 400, "invalid_json")
            return True
        _handle_compress(handler, body)
        return True

    # ── POST /tpk/v1/optimize ────────────────────────────────────────────
    if path == "/tpk/v1/optimize":
        body = _read_json_body(handler)
        if body is None:
            _send_error(handler, 400, "invalid_json")
            return True
        _handle_optimize(handler, body)
        return True

    # ── POST /tpk/v1/tokens/estimate ─────────────────────────────────────
    if path == "/tpk/v1/tokens/estimate":
        body = _read_json_body(handler)
        if body is None:
            _send_error(handler, 400, "invalid_json")
            return True
        _handle_tokens_estimate(handler, body)
        return True

    _send_error(handler, 404, "not_found", f"POST {path} not implemented yet")
    return True


def _read_json_body(handler: Any) -> Optional[dict[str, Any]]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_health(handler: Any) -> None:
    from tokenpak import __version__ as _version
    proxy_server = getattr(handler.server, "proxy_server", None)
    uptime = 0.0
    if proxy_server is not None:
        sess = getattr(proxy_server, "session", {}) or {}
        start = sess.get("start_time")
        if start:
            uptime = time.time() - start

    vault_info: dict[str, Any] = {"available": False, "blocks": 0}
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index
        vi = get_vault_index()
        if vi is not None and getattr(vi, "available", False):
            vault_info = {
                "available": True,
                "blocks": len(getattr(vi, "blocks", {}) or {}),
                "ready": bool(getattr(vi, "is_ready", lambda: True)()),
            }
    except Exception as exc:
        vault_info["error"] = str(exc)

    _send_json(handler, 200, {
        "version": _version,
        "uptime_s": round(uptime, 1),
        "vault": vault_info,
    })


def _handle_vault_search(handler: Any, qs: dict[str, list[str]]) -> None:
    query = (qs.get("q", [""])[0]).strip()
    if not query:
        _send_error(handler, 400, "missing_query", "provide ?q=<search>")
        return
    try:
        limit = int(qs.get("limit", ["5"])[0])
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))

    try:
        from tokenpak.proxy.vault_bridge import get_vault_index
        vi = get_vault_index()
    except Exception as exc:
        _send_error(handler, 503, "vault_unavailable", f"index init failed: {exc}")
        return
    if vi is None or not getattr(vi, "available", False):
        _send_error(handler, 503, "vault_unavailable", "no index.json found")
        return

    try:
        results = vi.search(query, top_k=limit)
    except Exception as exc:
        _send_error(handler, 500, "search_failed", str(exc))
        return

    out = []
    for block, score in results:
        block_id = block.get("block_id") or block.get("id") or ""
        source = block.get("source_type") or "vault"
        row = {
            "block_id": block_id,
            "path": block.get("path") or block.get("source_path", ""),
            "score": round(float(score), 3),
            "tokens": int(block.get("tokens", 0) or block.get("raw_tokens", 0) or 0),
            "preview": (block.get("title") or block.get("summary") or "")[:200],
            "source": source,
        }
        if source == "claude_transcript":
            ct = block.get("claude_transcript") or {}
            if ct:
                row["claude_transcript"] = {
                    "project_dir": ct.get("project_dir"),
                    "project_cwd_guess": ct.get("project_cwd_guess"),
                    "session_id": ct.get("session_id"),
                    "session_file": ct.get("session_file"),
                    "message_count": ct.get("message_count"),
                    "first_timestamp": ct.get("first_timestamp"),
                    "last_timestamp": ct.get("last_timestamp"),
                }
        out.append(row)
    _send_json(handler, 200, {"query": query, "count": len(out), "results": out})


def _handle_vault_block(handler: Any, block_id: str) -> None:
    if not block_id:
        _send_error(handler, 400, "missing_block_id")
        return

    try:
        from tokenpak.proxy.vault_bridge import get_vault_index
        vi = get_vault_index()
    except Exception as exc:
        _send_error(handler, 503, "vault_unavailable", f"index init failed: {exc}")
        return
    if vi is None or not getattr(vi, "available", False):
        _send_error(handler, 503, "vault_unavailable")
        return

    blocks = getattr(vi, "blocks", {}) or {}
    if block_id not in blocks:
        _send_error(handler, 404, "block_not_found", block_id)
        return

    meta = blocks[block_id]
    tokenpak_dir = getattr(vi, "tokenpak_dir", None)
    content = ""
    if tokenpak_dir:
        try:
            content_path = Path(tokenpak_dir) / "blocks" / f"{block_id}.txt"
            if content_path.exists():
                content = content_path.read_text(errors="replace")
        except Exception:
            pass

    _send_json(handler, 200, {
        "block_id": block_id,
        "path": meta.get("path", ""),
        "tokens": int(meta.get("tokens", 0) or 0),
        "content": content,
    })


# ---------------------------------------------------------------------------
# Budget + Journal — proxy-owned wrappers over the same SQLite files the
# companion's pre_send hook writes to (~/.tokenpak/companion/{budget,journal}.db).
# Both processes can share these via SQLite WAL mode.
# ---------------------------------------------------------------------------


def _companion_dir() -> Path:
    root = os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )
    return Path(root)


def _get_budget_tracker() -> Any:
    from tokenpak.companion.budget.tracker import BudgetTracker
    daily = 0.0
    try:
        daily = float(os.environ.get("TOKENPAK_COMPANION_BUDGET", "0") or 0)
    except ValueError:
        daily = 0.0
    return BudgetTracker(db_path=_companion_dir() / "budget.db", daily_budget=daily)


def _get_journal_store() -> Any:
    from tokenpak.companion.journal.store import JournalStore
    return JournalStore(db_path=_companion_dir() / "journal.db")


# ---------------------------------------------------------------------------
# Cross-tool handoff bridge.
#
# A per-tool session id is not retrievable by the other tool, so a journal
# entry alone cannot bridge two different clients (e.g. Claude Code ↔ Codex).
# The authoritative bridge is a shared handoff namespace under the companion
# run dir: ``current.json`` (latest pointer) + ``events.jsonl`` (append log).
# The reader pulls the latest without knowing the writer's session id. A
# readable current-session capsule is written alongside as the lossy
# summary/export layer (NOT the authority).
# ---------------------------------------------------------------------------

# Reserved capsule aliases that resolve to the newest real capsule by mtime,
# rather than a frozen on-disk symlink (which can silently go stale).
_CAPSULE_LATEST_ALIASES = {"active", "latest", "current"}


def _is_handoff(entry_type: str, content: str) -> bool:
    """A journal entry is a handoff if explicitly typed ``handoff`` or if it
    carries a HANDOFF_MARKER line. Content-sniffing keeps the model-facing
    journal_write tool unchanged (it always sends entry_type=user)."""
    if (entry_type or "").strip().lower() == "handoff":
        return True
    return "HANDOFF_MARKER" in (content or "")


def _extract_field(content: str, field: str) -> str:
    """Pull a ``FIELD: value`` line out of free-form handoff content."""
    needle = field + ":"
    for line in (content or "").splitlines():
        s = line.strip()
        if s.upper().startswith(needle):
            return s[len(needle):].strip()
    return ""


def _record_handoff(session_id: str, content: str) -> dict[str, Any]:
    """Write the authoritative shared handoff record and a readable
    current-session capsule. Returns the handoff record dict.

    Layout (under the companion run dir):
        run/handoff/current.json   — latest pointer (atomic tmp+replace)
        run/handoff/events.jsonl   — append-only event log
        capsules/<session_id>.md   — readable summary (newest → resolves `active`)
    """
    import hashlib

    cdir = _companion_dir()
    marker = _extract_field(content, "HANDOFF_MARKER") or ""
    secret = _extract_field(content, "SECRET_DECISION") or ""
    source_tool = os.environ.get("TOKENPAK_TOOL", "") or "unknown"
    now = time.time()
    iso = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat()
    basis = marker or content
    handoff_id = hashlib.sha256(basis.encode("utf-8", "ignore")).hexdigest()[:16]

    record = {
        "handoff_id": handoff_id,
        "session_id": session_id,
        "source_tool": source_tool,
        "updated_at": iso,
        "marker": marker,
        "secret_decision": secret,
        "summary": content[:2000],
        "payload_ref": f"journal:{session_id}",
    }

    hdir = cdir / "run" / "handoff"
    hdir.mkdir(parents=True, exist_ok=True)
    # current.json — atomic write
    tmp = hdir / "current.json.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(hdir / "current.json")
    # events.jsonl — append log
    with (hdir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Readable current-session capsule (newest mtime → resolves `active`)
    capdir = cdir / "capsules"
    capdir.mkdir(parents=True, exist_ok=True)
    cap_body = (
        f"# Handoff capsule — session {session_id}\n\n"
        f"- updated_at: {iso}\n"
        f"- source_tool: {source_tool}\n"
        f"- handoff_id: {handoff_id}\n\n"
        f"## Marker\n{marker or '(none)'}\n\n"
        f"## Secret decision\n{secret or '(none)'}\n\n"
        f"## Summary\n{content[:4000]}\n"
    )
    (capdir / f"{session_id}.md").write_text(cap_body, encoding="utf-8")

    return record


def _handle_budget_get(handler: Any, qs: dict[str, list[str]]) -> None:
    """Return current session + daily cost snapshot."""
    try:
        tracker = _get_budget_tracker()
        est = tracker.estimate(input_tokens=0)
    except Exception as exc:
        _send_error(handler, 500, "budget_unavailable", str(exc))
        return
    daily_budget = float(getattr(est, "daily_budget_usd", 0.0) or 0.0)
    remaining = getattr(est, "budget_remaining_usd", 0.0)
    # Sanitize inf/nan to None so strict JSON accepts them
    import math as _math
    try:
        remaining_val = float(remaining)
        if not _math.isfinite(remaining_val):
            remaining_val = None
    except (TypeError, ValueError):
        remaining_val = None
    _send_json(handler, 200, {
        "session_cost_usd": round(float(getattr(est, "session_total_usd", 0.0) or 0.0), 6),
        "daily_cost_usd": round(float(getattr(est, "daily_total_usd", 0.0) or 0.0), 6),
        "daily_budget_usd": round(daily_budget, 6),
        "remaining_usd": (round(remaining_val, 6) if remaining_val is not None else None),
        "session_requests": int(getattr(tracker, "session_requests", 0) or 0),
        "budget_set": daily_budget > 0,
    })


def _handle_journal_sessions(handler: Any, qs: dict[str, list[str]]) -> None:
    try:
        limit = int(qs.get("limit", ["10"])[0])
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(100, limit))
    try:
        store = _get_journal_store()
        sessions = store.recent_sessions(limit=limit)
    except Exception as exc:
        _send_error(handler, 500, "journal_unavailable", str(exc))
        return
    out = []
    for s in sessions:
        out.append({
            "session_id": getattr(s, "session_id", ""),
            "project_dir": getattr(s, "project_dir", ""),
            "total_requests": getattr(s, "total_requests", 0),
            "total_cost_usd": round(getattr(s, "total_cost_usd", 0.0), 6),
            "entry_count": getattr(s, "entry_count", 0),
        })
    _send_json(handler, 200, {"sessions": out})


def _handle_journal_get(handler: Any, session_id: str, qs: dict[str, list[str]]) -> None:
    if not session_id:
        _send_error(handler, 400, "missing_session_id")
        return
    entry_type = qs.get("entry_type", [None])[0]
    try:
        limit = int(qs.get("limit", ["20"])[0])
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(500, limit))
    try:
        store = _get_journal_store()
        entries = store.get_entries(session_id, entry_type=entry_type, limit=limit)
    except Exception as exc:
        _send_error(handler, 500, "journal_unavailable", str(exc))
        return
    _send_json(handler, 200, {
        "session_id": session_id,
        "entries": [
            {
                "timestamp": getattr(e, "timestamp", 0),
                "type": getattr(e, "entry_type", ""),
                "content": getattr(e, "content", ""),
            }
            for e in entries
        ],
    })


def _handle_journal_post(handler: Any, session_id: str, body: dict[str, Any]) -> None:
    if not session_id:
        _send_error(handler, 400, "missing_session_id")
        return
    content = str(body.get("content", "")).strip()
    if not content:
        _send_error(handler, 400, "missing_content")
        return
    entry_type = str(body.get("entry_type", "user")).strip() or "user"
    try:
        store = _get_journal_store()
        # Register the session row on first write so the sessions table is
        # populated and the journal session listing is meaningful. add_entry
        # alone never created a session row, which is why the table could be
        # empty despite entries existing.
        try:
            if store.get_session(session_id) is None:
                store.start_session(session_id, project_dir=os.getcwd())
        except Exception:
            pass  # registration is best-effort; never block the entry write
        store.add_entry(
            session_id=session_id,
            entry_type=entry_type,
            content=content,
        )
    except Exception as exc:
        _send_error(handler, 500, "journal_write_failed", str(exc))
        return
    # Mirror handoff facts to the authoritative shared namespace + a readable
    # current-session capsule so a different client can retrieve the latest
    # handoff without knowing the writer's per-tool session id.
    handoff = None
    if _is_handoff(entry_type, content):
        try:
            handoff = _record_handoff(session_id, content)
        except Exception:
            handoff = None  # mirroring is best-effort; the entry already landed
    out = {"status": "ok", "session_id": session_id, "entry_type": entry_type}
    if handoff:
        out["handoff"] = handoff
    _send_json(handler, 200, out)


# ---------------------------------------------------------------------------
# Pure-function endpoints — compress / optimize / tokens estimate.
# Stateless transformers; easy to extend with Pro-tier engines later.
# ---------------------------------------------------------------------------

_COMPANION_DEFAULT_INPUT_RATE_USD_PER_MTOK = 3.0


def _handle_compress(handler: Any, body: dict[str, Any]) -> None:
    """Head/tail truncate to fit max_tokens; record savings to journal when
    session_id is provided."""
    text = str(body.get("text", ""))
    try:
        max_tokens = int(body.get("max_tokens", 2000))
    except (TypeError, ValueError):
        max_tokens = 2000
    max_tokens = max(50, max_tokens)
    session_id = str(body.get("session_id", "")).strip()

    if not text:
        _send_error(handler, 400, "missing_text")
        return

    original_chars = len(text)
    original_tokens_est = max(1, original_chars // 4)
    max_chars = max_tokens * 4
    pruned = text
    if len(text) > max_chars:
        head_len = int(max_chars * 0.6)
        tail_len = int(max_chars * 0.3)
        head = text[:head_len].rsplit(" ", 1)[0] if " " in text[:head_len] else text[:head_len]
        tail = text[-tail_len:].split(" ", 1)[-1] if " " in text[-tail_len:] else text[-tail_len:]
        elided = original_chars - len(head) - len(tail)
        pruned = f"{head}\n\n[... {elided:,} chars elided ...]\n\n{tail}"

    pruned_tokens_est = max(1, len(pruned) // 4)
    tokens_avoided = max(0, original_tokens_est - pruned_tokens_est)
    cost_avoided = tokens_avoided * _COMPANION_DEFAULT_INPUT_RATE_USD_PER_MTOK / 1_000_000

    # Record savings on the proxy side so `tokenpak status` attributes
    # prompt-side value, matching the companion's prior behavior.
    if tokens_avoided > 0 and session_id:
        try:
            store = _get_journal_store()
            store.record_savings(
                session_id=session_id,
                tool="prune_context",
                tokens_avoided=tokens_avoided,
                cost_avoided_usd=cost_avoided,
                extra={"max_tokens": max_tokens},
            )
        except Exception:
            pass  # never fail the tool call

    reduction_pct = round((1 - pruned_tokens_est / original_tokens_est) * 100, 1)
    _send_json(handler, 200, {
        "pruned_text": pruned,
        "original_tokens": original_tokens_est,
        "pruned_tokens": pruned_tokens_est,
        "tokens_avoided": tokens_avoided,
        "cost_avoided_usd": round(cost_avoided, 6),
        "reduction_pct": reduction_pct,
    })


def _handle_optimize(handler: Any, body: dict[str, Any]) -> None:
    """Run the offline prompt-optimization analyzer."""
    text = str(body.get("text", ""))
    if not text.strip():
        _send_error(handler, 400, "missing_text")
        return
    source = str(body.get("source", "<http>"))
    try:
        from tokenpak.cli.commands.optimize_prompt import analyze
        report = analyze(text, source=source)
    except Exception as exc:
        _send_error(handler, 500, "optimize_failed", str(exc))
        return
    _send_json(handler, 200, report.to_dict())


def _handle_capsules_list(handler: Any, qs: dict[str, list[str]]) -> None:
    """List available capsules in the companion capsule directory."""
    try:
        limit = int(qs.get("limit", ["10"])[0])
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(100, limit))
    capsule_dir = _companion_dir() / "capsules"
    if not capsule_dir.exists():
        _send_json(handler, 200, {"capsules": [], "message": "No capsules stored yet."})
        return
    items = []
    files = sorted(capsule_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    for p in files[:limit]:
        try:
            st = p.stat()
            items.append({
                "session_id": p.stem,
                "size_bytes": st.st_size,
                "modified": st.st_mtime,
            })
        except OSError:
            continue
    _send_json(handler, 200, {"capsules": items})


def _handle_capsule_get(handler: Any, session_id: str, qs: dict[str, list[str]]) -> None:
    """Return a specific capsule's content (first match on session_id substring)."""
    if not session_id:
        _send_error(handler, 400, "missing_session_id")
        return
    capsule_dir = _companion_dir() / "capsules"
    if not capsule_dir.exists():
        _send_error(handler, 404, "capsule_not_found", session_id)
        return
    try:
        from tokenpak.companion.capsules.builder import load_capsule
    except Exception as exc:
        _send_error(handler, 500, "capsule_module_unavailable", str(exc))
        return
    match = None
    if session_id.strip().lower() in _CAPSULE_LATEST_ALIASES:
        # Reserved aliases resolve to the NEWEST real capsule by mtime, not an
        # on-disk alias file (a frozen symlink keeps its target's mtime and can
        # silently serve a stale handoff). Skip the alias files themselves so
        # we never resolve back to a stale target.
        candidates = sorted(
            (p for p in capsule_dir.glob("*.md")
             if p.name not in ("active.md", "latest.md", "current.md")),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        match = candidates[0] if candidates else None
    else:
        for p in capsule_dir.glob("*.md"):
            if session_id in p.stem:
                match = p
                break
    if match is None:
        _send_error(handler, 404, "capsule_not_found", session_id)
        return
    try:
        content = load_capsule(str(match))
    except Exception as exc:
        _send_error(handler, 500, "capsule_load_failed", str(exc))
        return
    tokens_est = max(1, len(content or "") // 4)

    # Also record a "load_capsule" savings event if the caller identified
    # their session via ?session_id= or X-TP-Session header (preserves the
    # old in-process behavior where the companion wrote this to journal).
    caller_sid = qs.get("caller_session_id", [""])[0] or handler.headers.get("X-TP-Session", "")
    if caller_sid:
        try:
            store = _get_journal_store()
            store.record_savings(
                session_id=caller_sid,
                tool="load_capsule",
                tokens_avoided=0,
                cost_avoided_usd=0.0,
                extra={"capsule_session": match.stem, "capsule_tokens_est": tokens_est},
            )
        except Exception:
            pass

    _send_json(handler, 200, {
        "session_id": match.stem,
        "path": str(match),
        "tokens_est": tokens_est,
        "content": content or "",
    })


def _handle_models_list(handler: Any, qs: dict[str, list[str]]) -> None:
    """Return known models = seed catalog + observed-in-traffic (monitor.db).

    Observed-in-traffic models get resolved via family-rule inference so
    their pricing is accurate even if they're not in the seed catalog.
    This is what lets `claude-opus-4-7` show up in the list despite
    never being hand-added — we saw real requests for it, and family
    rules know it's opus-tier.

    Used by OpenClaw adapter + external tools to stay in sync with the
    living reality of what's actually being used.

    Query params:
      ?provider=anthropic         filter to one provider
      ?include_observed=0         exclude the monitor.db augmentation
    """
    filter_provider = (qs.get("provider", [""])[0] or "").strip().lower()
    include_observed = (qs.get("include_observed", ["1"])[0] or "1") != "0"

    try:
        from tokenpak.models import get_registry
        reg = get_registry()
        registered = list(reg.all_models())
    except Exception as exc:
        _send_error(handler, 500, "registry_unavailable", str(exc))
        return

    known_ids = {m.model_id for m in registered}

    # Augment from monitor.db observed-in-traffic models (opus-4-7 et al)
    observed_ids: list[str] = []
    if include_observed:
        try:
            import sqlite3 as _sq
            db_path = os.environ.get(
                "TOKENPAK_DB",
                os.path.expanduser("~/.tokenpak/monitor.db"),
            )
            if os.path.exists(db_path):
                c = _sq.connect(db_path)
                rows = c.execute(
                    "SELECT DISTINCT model FROM requests "
                    "WHERE model IS NOT NULL AND model != '' "
                    "  AND timestamp >= datetime('now', '-30 days')"
                ).fetchall()
                c.close()
                observed_ids = [r[0] for r in rows if r[0] and r[0] not in known_ids]
        except Exception:
            pass

    # Resolve observed IDs via registry (family rules populate pricing)
    observed_resolved = []
    for mid in observed_ids:
        try:
            info = reg.resolve(mid)
            observed_resolved.append(info)
        except Exception:
            continue

    all_models = registered + observed_resolved

    out = []
    for m in all_models:
        if filter_provider and (m.provider or "").lower() != filter_provider:
            continue
        out.append({
            "id": m.model_id,
            "provider": m.provider,
            "tier": m.tier,
            "input_per_mtok": m.input_per_mtok,
            "output_per_mtok": m.output_per_mtok,
            "cache_read_per_mtok": m.cache_read_per_mtok,
            "cache_write_per_mtok": m.cache_write_per_mtok,
            "source": m.source,
            "aliases": list(m.aliases or []),
        })

    # Sort: by source (seed → discovered → inferred/observed), then id desc
    _src_order = {"seed": 0, "discovered": 1, "inferred": 2}
    out.sort(key=lambda m: (_src_order.get(m["source"], 9), m["id"]))
    _send_json(handler, 200, {"count": len(out), "models": out})


def _handle_session_info_get(handler: Any) -> None:
    """Proxy-side snapshot of process state + vault + active session counters."""
    from tokenpak import __version__ as _version
    proxy_server = getattr(handler.server, "proxy_server", None)
    uptime = 0.0
    session_stats: dict[str, Any] = {}
    if proxy_server is not None:
        sess = getattr(proxy_server, "session", {}) or {}
        start = sess.get("start_time")
        if start:
            uptime = time.time() - start
        session_stats = {
            "requests": int(sess.get("requests", 0) or 0),
            "input_tokens": int(sess.get("input_tokens", 0) or 0),
            "output_tokens": int(sess.get("output_tokens", 0) or 0),
            "cost_usd": round(float(sess.get("cost", 0.0) or 0.0), 6),
            "cost_saved_usd": round(float(sess.get("cost_saved", 0.0) or 0.0), 6),
            "errors": int(sess.get("errors", 0) or 0),
        }
    vault_info: dict[str, Any] = {"available": False, "blocks": 0}
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index
        vi = get_vault_index()
        if vi is not None and getattr(vi, "available", False):
            vault_info = {
                "available": True,
                "blocks": len(getattr(vi, "blocks", {}) or {}),
            }
    except Exception:
        pass
    compilation_mode = os.environ.get("TOKENPAK_MODE", "hybrid")
    profile = os.environ.get("TOKENPAK_PROFILE", "balanced")
    cache_ttl = os.environ.get("TOKENPAK_CACHE_TTL", "5m").strip() or "5m"
    _send_json(handler, 200, {
        "version": _version,
        "uptime_s": round(uptime, 1),
        "mode": compilation_mode,
        "profile": profile,
        "cache_ttl": cache_ttl,
        "session": session_stats,
        "vault": vault_info,
    })


def _handle_tokens_estimate(handler: Any, body: dict[str, Any]) -> None:
    text = str(body.get("text", ""))
    file_path = str(body.get("file_path", "")).strip()
    if file_path:
        p = Path(file_path)
        if not p.exists():
            _send_error(handler, 404, "file_not_found", file_path)
            return
        try:
            text = p.read_text(errors="replace")
        except Exception as exc:
            _send_error(handler, 500, "file_read_failed", str(exc))
            return
    if not text:
        _send_error(handler, 400, "missing_text")
        return
    chars = len(text)
    try:
        from tokenpak.telemetry.tokens import count_tokens
        CHUNK = 100_000
        if chars <= CHUNK:
            tokens = count_tokens(text)
        else:
            tokens = sum(count_tokens(text[i:i + CHUNK]) for i in range(0, chars, CHUNK))
    except Exception:
        tokens = max(1, chars // 4)
    _send_json(handler, 200, {
        "chars": chars,
        "tokens": int(tokens),
        "chars_per_token": round(chars / max(1, tokens), 2),
    })


# ---------------------------------------------------------------------------
# /pak/v1/* — MultiPak Pro daemon protocol surface
# ---------------------------------------------------------------------------
#
# The /pak/v1/* namespace is distinct from /tpk/v1/* by design:
#
#   /tpk/v1/*  — OSS app API (proxy-owned vault/budget/journal/capsules).
#                Glossary: ``api-tpk-v1``. Stable contract for OSS clients.
#
#   /pak/v1/*  — Pro daemon protocol surface. Stubs return ``not_implemented``
#                when the closed-source ``tokenpak-paid-daemon`` is absent.
#                /pak/v1/status always works (diagnostic surface).
#
# Phase 1 OSS ships read-only Vault Pak inspection via the adapter; everything
# else is a 501 stub gated on daemon presence per the daemon fallback contract.


def _try_handle_pak_get(handler: Any, path: str, parsed: Any) -> bool:
    """Dispatch GET requests under /pak/v1/*.

    Returns True after sending a response (sets the contract that the
    proxy's main GET handler must not fall through). Authorization gate
    is the same as /tpk/v1/* — localhost + optional X-TPK-Key.
    """
    if not _is_authorized(handler):
        _send_error(
            handler,
            401,
            "unauthorized",
            "localhost-only; set X-TPK-Key if TOKENPAK_PROXY_KEY is configured",
        )
        return True

    qs = parse_qs(parsed.query or "")

    # ── /pak/v1/status ──────────────────────────────────────────────────
    # Diagnostic surface — works regardless of daemon presence. Mirrors the
    # `tokenpak pak status` CLI.
    if path == "/pak/v1/status":
        _handle_pak_status(handler)
        return True

    # ── /pak/v1/list ─────────────────────────────────────────────────────
    # Paginated listing of recall-store Pak metadata rows. OSS read path.
    # Supports filter-by-project and filter-by-pak_type (both byte-literal),
    # cursor-based pagination, and a hard cap of LIST_LIMIT_MAX rows/page.
    if path == "/pak/v1/list":
        _handle_pak_list(handler, qs)
        return True

    # ── /pak/v1/inspect/<pak-id> ────────────────────────────────────────
    # Read-only Pak inspection. Vault Paks (pak_id starts with "vault:")
    # are served via the OSS adapter; other ``pak_id`` values fall back to a
    # recall-store metadata lookup and return 404 if unknown.
    # ``#`` is URL-significant (fragment delimiter) and is mandatory in
    # vault block IDs — clients MUST percent-encode as ``%23``; the handler
    # decodes via :func:`urllib.parse.unquote` to recover the canonical id.
    if path.startswith("/pak/v1/inspect/"):
        from urllib.parse import unquote

        pak_id = unquote(path[len("/pak/v1/inspect/"):])
        _handle_pak_inspect(handler, pak_id)
        return True

    _send_error(
        handler,
        404,
        "not_found",
        f"unknown /pak/v1 endpoint: {path}",
    )
    return True


def _try_handle_pak_post(handler: Any, path: str) -> bool:
    """Dispatch POST requests under /pak/v1/*."""
    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized")
        return True

    # ── POST /pak/v1/promote ────────────────────────────────────────────
    # Pro-gated capture pipeline. When the Pro daemon is reachable, forward
    # the request body to the daemon's loopback port and proxy the response
    # back. When the daemon is absent or unreachable, return the standardized
    # 501 ``pro_daemon_required``.
    if path == "/pak/v1/promote":
        _handle_pak_promote_forward(handler)
        return True

    # ── POST /pak/v1/recall ─────────────────────────────────────────────
    # Pro-only. Always 501 in OSS; the daemon owns ranking, hydration, and
    # packaging.
    if path == "/pak/v1/recall":
        _send_pak_not_implemented(
            handler,
            reason="pro_daemon_required",
            detail="Recall ranking is a Pro-only feature; install tokenpak-paid to enable.",
        )
        return True

    _send_error(
        handler,
        404,
        "not_found",
        f"POST /pak/v1{path[len('/pak/v1'):]} not implemented yet",
    )
    return True


def _handle_pak_promote_forward(handler: Any) -> None:
    """Forward POST /pak/v1/promote to the Pro daemon's loopback port.

    The capture pipeline lives in the closed-source daemon. The OSS proxy's
    job here is to:

    1. Probe daemon presence via ``daemon_probe.detect_daemon_state``.
    2. If absent → return 501 ``pro_daemon_required``.
    3. If present → read the daemon's port from sock-info, POST the
       request body to the daemon's ``/pak/v1/promote``, stream the
       response back.

    Defensive details:

    - The auth headers from the caller are NOT forwarded to the daemon.
      The daemon is loopback-only by construction, so it requires no auth
      on /pak/v1/*; passing the OSS caller's auth headers through would
      leak them into the daemon's logs needlessly.
    - Short timeout (5s). The daemon is local; if it's not responding
      within that, we treat it as TOCTOU race (probe said active,
      daemon stopped between probe and forward) and return 503.
    - Body is read once, fully, before forwarding. We don't stream —
      the capture pipeline payloads are small (<<1MB).
    """
    import http.client

    from tokenpak.licensing.daemon_probe import (
        detect_daemon_state,
        sock_info_path,
    )

    state = detect_daemon_state()
    if state != "active":
        _send_pak_not_implemented(
            handler,
            reason="pro_daemon_required",
            detail=(
                "Capture pipeline is a Pro-only feature; install tokenpak-paid "
                "and start the daemon to enable."
            ),
        )
        return

    # Read sock-info to discover the daemon's port.
    try:
        info_raw = sock_info_path().read_text(encoding="utf-8")
        info = json.loads(info_raw)
        daemon_port = int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        # Sock-info disappeared or malformed between probe and read.
        _send_pak_not_implemented(
            handler,
            reason="pro_daemon_required",
            detail="Daemon sock-info missing or malformed at forward time.",
        )
        return

    # Read the inbound body.
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        length = 0
    body = handler.rfile.read(length) if length > 0 else b""

    # Forward to daemon. Auth headers are intentionally NOT propagated.
    conn = http.client.HTTPConnection("127.0.0.1", daemon_port, timeout=5.0)
    try:
        conn.request(
            "POST",
            "/pak/v1/promote",
            body=body,
            headers={
                "Content-Type": handler.headers.get("Content-Type", "application/json"),
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        resp_body = resp.read()
        # Mirror the daemon's status code and content-type.
        handler.send_response(resp.status)
        ct = resp.getheader("Content-Type") or "application/json; charset=utf-8"
        handler.send_header("Content-Type", ct)
        handler.send_header("Content-Length", str(len(resp_body)))
        handler.send_header("X-TokenPak-Forwarded-From-Daemon", "1")
        handler.end_headers()
        handler.wfile.write(resp_body)
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        # TOCTOU — daemon went away between probe and forward, or
        # connection failed. Treat as 503 so the caller can retry; not
        # 501, because we KNOW the daemon should be there.
        _send_json(
            handler,
            503,
            {
                "error": "daemon_unreachable",
                "detail": (
                    f"Pro daemon was reachable at probe time but request failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "suggested_action": (
                    "Retry the request; if the failure persists, restart the "
                    "tokenpak-paid daemon."
                ),
            },
        )
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover — defensive
            pass


def _send_pak_not_implemented(
    handler: Any,
    *,
    reason: str,
    detail: str,
    suggested_action: str = "Install tokenpak-paid (Pro) to enable this surface.",
) -> None:
    """Standardized 501 response for Pro-gated /pak/v1/* endpoints.

    The shape is stable across all Pro-gated endpoints so clients can
    treat ``error == "not_implemented"`` + ``reason`` as the canonical
    "daemon absent" signal. ``daemon_state`` mirrors the daemon
    fallback-contract telemetry so callers can disambiguate "Pro never
    installed" from "Pro present but version-incompatible" once Phase 2
    ships.
    """
    from tokenpak.licensing.daemon_probe import detect_daemon_state

    _send_json(
        handler,
        501,
        {
            "error": "not_implemented",
            "reason": reason,
            "detail": detail,
            "suggested_action": suggested_action,
            "daemon_state": detect_daemon_state(),
        },
    )


def _handle_pak_list(handler: Any, qs: dict[str, list[str]]) -> None:
    """GET /pak/v1/list — paginated metadata listing from the recall store.

    Query parameters (all optional):
        project     — filter rows by exact ``project`` value (byte-literal).
        pak_type    — filter rows by exact ``pak_type`` value (byte-literal).
        limit       — page size; clamped to ``[1, LIST_LIMIT_MAX]`` (default
                      ``LIST_LIMIT_DEFAULT``). Invalid integers yield 400.
        cursor      — opaque token returned by a previous response; resumes
                      after the row it identifies. Invalid cursors yield 400.

    Response shape (always — including the empty page):

        {
          "items":       [<pak metadata dict>, ...],
          "next_cursor": "<token>" | null,
          "limit":       <int, the effective limit>,
          "truncated":   true | false
        }

    ``items`` is metadata only: no body bytes, no anchors, no full text.
    ``next_cursor`` is non-null whenever more rows match the filters than
    fit on this page (i.e. ``truncated`` is true). When ``truncated`` is
    false, ``next_cursor`` is always null.
    """
    # Parse + clamp limit ------------------------------------------------------
    limit_raw = (qs.get("limit", [""]) or [""])[0]
    if limit_raw == "":
        limit_arg = LIST_LIMIT_DEFAULT
    else:
        try:
            limit_arg = int(limit_raw)
        except ValueError:
            _send_error(
                handler,
                400,
                "invalid_request",
                f"limit must be an integer (got {limit_raw!r})",
            )
            return
        if limit_arg < 1:
            _send_error(
                handler,
                400,
                "invalid_request",
                "limit must be >= 1",
            )
            return

    project = (qs.get("project", [None]) or [None])[0]
    pak_type = (qs.get("pak_type", [None]) or [None])[0]
    cursor = (qs.get("cursor", [None]) or [None])[0]

    filters = PakListFilters(
        project=project,
        pak_type=pak_type,
        limit=limit_arg,
        cursor=cursor,
    )

    try:
        store = _open_recall_store_default()
    except Exception as exc:
        _send_error(
            handler,
            500,
            "internal_error",
            f"recall store unavailable: {exc}",
        )
        return

    try:
        try:
            result = store.list_paks(filters)
        except ValueError as exc:
            # Invalid cursor surface — keep client-facing wording neutral.
            _send_error(handler, 400, "invalid_request", str(exc))
            return
    finally:
        store.close()

    _send_json(
        handler,
        200,
        {
            "items": [_pak_row_to_dict(r) for r in result.items],
            "next_cursor": result.next_cursor,
            "limit": result.limit,
            "truncated": result.truncated,
        },
    )


def _pak_row_to_dict(row: "PakRow") -> dict[str, Any]:
    """Serialise a :class:`PakRow` into the wire-shape dict.

    Field set is exactly the ``paks`` schema columns — no derived fields,
    no body bytes, no anchor refs. Nullable columns surface as ``null``
    via the standard JSON encoder.
    """
    return {
        "pak_id": row.pak_id,
        "pak_type": row.pak_type,
        "project": row.project,
        "topic": row.topic,
        "source_type": row.source_type,
        "authority": row.authority,
        "title": row.title,
        "summary": row.summary,
        "content_hash": row.content_hash,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "superseded_by": row.superseded_by,
    }


def _open_recall_store_default() -> "RecallStore":
    """Open the recall store at its default location.

    Wrapped so test code can monkey-patch this single function rather
    than reach into ``tokenpak.companion.recall`` directly.
    """
    from tokenpak.companion.recall import open_recall_store

    return open_recall_store()


def _recall_get_pak(pak_id: str) -> Optional["PakRow"]:
    """Convenience: open store, fetch one row, close store.

    Used by ``_handle_pak_inspect`` non-vault fallback. Lifted out so
    tests can patch the open path independently from the inspect logic.
    """
    store = _open_recall_store_default()
    try:
        return store.get_pak(pak_id)
    finally:
        store.close()


def _handle_pak_status(handler: Any) -> None:
    """GET /pak/v1/status — diagnostic snapshot.

    Always-on: works without daemon, without ``multipak.enabled``, without
    a configured Pak store. Provides the four signals a CLI / dashboard
    needs to render Pro readiness:

    - ``daemon_state`` — fallback-contract value emitted by the probe.
    - ``multipak_enabled`` — config flag (``pro.multipak.enabled``).
    - ``pak_store_present`` — does ``~/.tokenpak/pro/state/multipak/``
      exist on disk?
    - ``vault_paks_indexed`` — block count from the OSS vault index.
    - ``promotion_candidates`` — count of journal entries marked ready
      for daemon-side promotion.
    """
    from tokenpak.licensing.daemon_probe import detect_daemon_state

    state = detect_daemon_state()

    # multipak.enabled config flag — read dynamically per
    # ``feedback_always_dynamic.md``. The flag lives under ``pro.multipak.
    # enabled`` in ``~/.tokenpak/config.yaml``. Default false until soak.
    multipak_enabled = _read_multipak_enabled()

    # Pak store presence — directory existence is sufficient signal.
    pak_store_dir = Path.home() / ".tokenpak" / "pro" / "state" / "multipak"
    pak_store_present = pak_store_dir.is_dir()

    # Vault index block count (best-effort — empty when index unavailable).
    vault_paks_indexed = _vault_block_count()

    # Promotion-candidate count (best-effort — zero when journal absent).
    promotion_candidates = _promotion_candidate_count()

    _send_json(
        handler,
        200,
        {
            "daemon_state": state,
            "multipak_enabled": multipak_enabled,
            "pak_store_present": pak_store_present,
            "vault_paks_indexed": vault_paks_indexed,
            "promotion_candidates": promotion_candidates,
        },
    )


def _handle_pak_inspect(handler: Any, pak_id: str) -> None:
    """GET /pak/v1/inspect/<pak-id> — return a Pak's serialized form.

    Resolution order:

    1. If ``pak_id`` starts with ``vault:`` the OSS Vault Pak adapter
       serves it from the vault index. Returns 404 if the underlying
       block isn't indexed.
    2. Otherwise the OSS recall store is consulted for a metadata row.
       Returns 200 with the row's metadata if present, 404 if not.

    No surface beyond this lookup is exposed by the OSS endpoint — the
    body bytes of a Pak are not part of the metadata index by design
    (the index is content-hash-keyed, not content-bearing).
    """
    if not pak_id:
        _send_error(handler, 400, "invalid_request", "pak_id required")
        return

    if pak_id.startswith("vault:"):
        # The vault adapter wraps a vault block by id; for now we fetch
        # the underlying block via the existing vault-bridge helper and
        # convert. Returns 404 when the block isn't indexed.
        block_id = pak_id[len("vault:"):]
        try:
            from tokenpak.proxy.vault_bridge import get_vault_index

            vi = get_vault_index()
            if vi is None:
                _send_error(
                    handler,
                    503,
                    "vault_unavailable",
                    "vault index not loaded",
                )
                return
            blocks = getattr(vi, "blocks", None) or {}
            block_dict = blocks.get(block_id)
            if block_dict is None:
                _send_error(
                    handler,
                    404,
                    "pak_not_found",
                    f"vault block not indexed: {block_id}",
                )
                return
            from tokenpak.vault.pak_adapter import vault_block_to_pak

            pak = vault_block_to_pak(block_dict)
            _send_json(handler, 200, pak.to_dict())
            return
        except Exception as exc:
            _send_error(
                handler,
                500,
                "internal_error",
                f"vault inspect failed: {exc}",
            )
            return

    # Non-vault id — consult the OSS recall metadata store. A hit returns
    # the metadata row; a miss returns 404. No further fallback in OSS.
    try:
        row = _recall_get_pak(pak_id)
    except Exception as exc:
        _send_error(
            handler,
            500,
            "internal_error",
            f"recall lookup failed: {exc}",
        )
        return

    if row is None:
        _send_error(
            handler,
            404,
            "pak_not_found",
            f"no Pak with pak_id {pak_id!r}",
        )
        return

    _send_json(handler, 200, _pak_row_to_dict(row))


def _read_multipak_enabled() -> bool:
    """Read ``pro.multipak.enabled`` from ``~/.tokenpak/config.yaml``.

    Default ``False`` (opt-in until the 1-week soak post-bootstrap
    completes). Resilient to missing/malformed config — never raises;
    failures degrade to ``False``.

    Lookup order:
    1. ``pro.multipak.enabled`` (canonical Pro-config layout).
    2. ``multipak.enabled`` (legacy/unscoped fallback — accepted but not
       canonical; configs should migrate).
    """
    try:
        from tokenpak.core.config_loader import load_config
    except ImportError:
        return False
    try:
        cfg = load_config()
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    pro = cfg.get("pro")
    if isinstance(pro, dict):
        mp = pro.get("multipak")
        if isinstance(mp, dict):
            v = mp.get("enabled")
            if isinstance(v, bool):
                return v
    # Legacy fallback path
    mp = cfg.get("multipak")
    if isinstance(mp, dict):
        v = mp.get("enabled")
        if isinstance(v, bool):
            return v
    return False


def _vault_block_count() -> int:
    """Best-effort vault index block count for /pak/v1/status.

    Returns 0 when the vault subsystem isn't loaded — the surface should
    work even on hosts without a vault index. Never raises.
    """
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index

        vi = get_vault_index()
        if vi is None:
            return 0
        return len(getattr(vi, "blocks", {}) or {})
    except Exception:
        return 0


def _promotion_candidate_count() -> int:
    """Count of journal entries marked as Pak promotion candidates.

    Reads from the canonical companion journal DB at
    ``~/.tokenpak/companion/journal.db``. Returns 0 when the DB is
    absent or unreadable. Never raises.
    """
    db_path = Path.home() / ".tokenpak" / "companion" / "journal.db"
    if not db_path.exists():
        return 0
    try:
        from tokenpak.companion.journal.pak_aware import count_promotion_candidates

        return count_promotion_candidates(db_path)
    except Exception:
        return 0
