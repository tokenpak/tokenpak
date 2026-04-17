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
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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

    _send_error(handler, 404, "not_found", f"unknown app endpoint: {path}")
    return True


def try_handle_post(handler: Any) -> bool:
    """POST dispatch — reserved for future endpoints (compress, optimize, etc.).

    Return False if path doesn't match so default dispatch continues.
    """
    parsed = urlparse(handler.path)
    path = parsed.path
    if not path.startswith("/tp/v1/"):
        return False

    if not _is_authorized(handler):
        _send_error(handler, 401, "unauthorized")
        return True

    _send_error(handler, 404, "not_found", f"POST {path} not implemented yet")
    return True


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
