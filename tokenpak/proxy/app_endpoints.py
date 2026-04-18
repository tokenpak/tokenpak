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
    """If handler.path starts with /tpk/v1/, handle it and return True.

    Return False to let the default dispatch continue.
    """
    parsed = urlparse(handler.path)
    path = parsed.path
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
