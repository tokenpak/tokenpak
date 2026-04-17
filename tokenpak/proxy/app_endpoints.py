# SPDX-License-Identifier: Apache-2.0
"""App-level REST endpoints under ``/tp/v1/*``.

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
      is set, in which case an ``X-TP-Key`` header must match.
    - No CORS / cross-origin story today; GTM is single-host dev use.

Error shape:
    {"error": "<code>", "detail": "<human message>"}
    Status codes match HTTP semantics (400 malformed, 401 unauthorized,
    404 not found, 500 internal, 503 index not loaded).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _is_authorized(handler: Any) -> bool:
    """Check localhost + optional X-TP-Key header."""
    # Localhost gate — reject non-loopback by default
    client_ip = getattr(handler, "client_address", ("", 0))[0] or ""
    if client_ip not in ("127.0.0.1", "::1", "localhost", ""):
        return False

    key = os.environ.get("TOKENPAK_PROXY_KEY", "").strip()
    if not key:
        return True
    return handler.headers.get("X-TP-Key", "") == key


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
    """If handler.path starts with /tp/v1/, handle it and return True.

    Return False to let the default dispatch continue.
    """
    parsed = urlparse(handler.path)
    path = parsed.path
    if not path.startswith("/tp/v1/"):
        return False

    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized", "localhost-only; set X-TP-Key if TOKENPAK_PROXY_KEY is configured")
        return True

    qs = parse_qs(parsed.query or "")

    # ── /tp/v1/health ────────────────────────────────────────────────────
    if path == "/tp/v1/health":
        _handle_health(handler)
        return True

    # ── /tp/v1/vault/search?q=...&limit=N ───────────────────────────────
    if path == "/tp/v1/vault/search":
        _handle_vault_search(handler, qs)
        return True

    # ── /tp/v1/vault/block/{block_id} ───────────────────────────────────
    if path.startswith("/tp/v1/vault/block/"):
        block_id = path[len("/tp/v1/vault/block/"):]
        _handle_vault_block(handler, block_id)
        return True

    # ── /tp/v1/budget ────────────────────────────────────────────────────
    if path == "/tp/v1/budget":
        _handle_budget_get(handler, qs)
        return True

    # ── /tp/v1/journal/sessions ─────────────────────────────────────────
    if path == "/tp/v1/journal/sessions":
        _handle_journal_sessions(handler, qs)
        return True

    # ── /tp/v1/journal/{session_id} ─────────────────────────────────────
    if path.startswith("/tp/v1/journal/"):
        session_id = path[len("/tp/v1/journal/"):]
        _handle_journal_get(handler, session_id, qs)
        return True

    _send_error(handler, 404, "not_found", f"unknown app endpoint: {path}")
    return True


def try_handle_post(handler: Any) -> bool:
    """POST dispatch for app endpoints that accept a body."""
    parsed = urlparse(handler.path)
    path = parsed.path
    if not path.startswith("/tp/v1/"):
        return False

    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized")
        return True

    # ── POST /tp/v1/journal/{session_id}/entry ──────────────────────────
    if path.startswith("/tp/v1/journal/") and path.endswith("/entry"):
        session_id = path[len("/tp/v1/journal/"):-len("/entry")]
        body = _read_json_body(handler)
        if body is None:
            _send_error(handler, 400, "invalid_json", "request body must be JSON")
            return True
        _handle_journal_post(handler, session_id, body)
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
        out.append({
            "block_id": block_id,
            "path": block.get("path", ""),
            "score": round(float(score), 3),
            "tokens": int(block.get("tokens", 0) or 0),
            "preview": (block.get("title") or block.get("summary") or "")[:200],
        })
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
        store.add_entry(
            session_id=session_id,
            entry_type=entry_type,
            content=content,
        )
    except Exception as exc:
        _send_error(handler, 500, "journal_write_failed", str(exc))
        return
    _send_json(handler, 200, {"status": "ok", "session_id": session_id, "entry_type": entry_type})
