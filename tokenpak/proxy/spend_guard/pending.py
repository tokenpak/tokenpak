# SPDX-License-Identifier: Apache-2.0
"""Pending request store — TTL-bounded SQLite-backed holding cell.

When the policy engine returns ``decision=block``, the original request body
+ headers + target URL are stored here keyed by ``session_id``. The caller
gets a structured block response. The held request is replayed verbatim
(byte-preserving) when the user approves via Yes intent, ``[TIP: allow=once]``,
or expires after ``pending_ttl_seconds``.

DB layout follows the ``monitor.db`` / ``budget.db`` convention:
- One file at ``~/.tokenpak/spend_guard.db`` (configurable).
- Lazy CREATE TABLE IF NOT EXISTS.
- Per-call ``sqlite3.connect`` — no pool. WAL not enabled (writes are infrequent).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .contracts import PendingRequest

# Credential-bearing request headers are NEVER persisted to spend_guard.db.
# The held request is replayed with the live approving request's own auth
# (the proxy re-applies it — see proxy/server.py replay-merge), so dropping
# these from storage is safe and keeps raw credentials off disk, matching
# the proxy's "zero disk writes" passthrough contract for credentials.
_SENSITIVE_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "openai-api-key",
    "anthropic-api-key",
    "x-goog-api-key",
    "cookie",
    "set-cookie",
})


def redact_headers(headers: dict) -> dict:
    """Return a copy of ``headers`` with credential-bearing headers removed.

    Case-insensitive on the header name. Used at store time (so creds never
    reach disk) and defensively at replay time.
    """
    if not headers:
        return {}
    return {
        k: v for k, v in headers.items()
        if str(k).lower() not in _SENSITIVE_HEADERS
    }


def _db_path(audit_db_path: str) -> Path:
    """Expand and ensure parent dir exists (owner-only)."""
    p = Path(os.path.expanduser(audit_db_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    return p


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # The db holds request metadata — keep it owner-only (0600), like
    # credentials.toml. Best-effort; never fail a connect over perms.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema setup. Adds missing columns as the schema evolves."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_requests (
            pending_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            request_hash TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            projected_tokens INTEGER NOT NULL DEFAULT 0,
            projected_cost_usd REAL NOT NULL DEFAULT 0.0,
            raw_request_blob BLOB NOT NULL,
            raw_request_headers TEXT NOT NULL DEFAULT '{}',
            target_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_session ON pending_requests(session_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_hash ON pending_requests(request_hash, status)"
    )
    # One-time migration: redact credential headers from any rows written by
    # an older build that persisted raw headers (raw credential-on-disk
    # exposure). Gated by PRAGMA user_version so it runs exactly once per db;
    # no rows are deleted, so held requests stay replayable.
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        _redact_existing_rows(conn)
        conn.execute("PRAGMA user_version = 1")
    conn.commit()


def _redact_existing_rows(conn: sqlite3.Connection) -> None:
    """Rewrite ``raw_request_headers`` in place, dropping credential headers.

    No rows are deleted — held requests stay replayable (replay uses the live
    approving request's auth, not the persisted copy).
    """
    rows = conn.execute(
        "SELECT pending_id, raw_request_headers FROM pending_requests"
    ).fetchall()
    for row in rows:
        try:
            hdrs = json.loads(row["raw_request_headers"] or "{}")
        except (ValueError, TypeError):
            hdrs = {}
        if not isinstance(hdrs, dict):
            hdrs = {}
        redacted = redact_headers(hdrs)
        if redacted != hdrs:
            conn.execute(
                "UPDATE pending_requests SET raw_request_headers = ? "
                "WHERE pending_id = ?",
                (json.dumps(redacted, default=str), row["pending_id"]),
            )


def hash_request(body: bytes, model: str) -> str:
    """Stable hash for anti-loop dedup."""
    h = hashlib.blake2b(digest_size=16)
    h.update(model.encode())
    h.update(b"\x00")
    h.update(body)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PendingStore:
    """SQLite-backed pending-request store.

    Instances are cheap — they hold only a path. Each method opens a fresh
    connection to keep the proxy thread-safe (BaseHTTPServer is per-request).
    """

    def __init__(self, audit_db_path: str = "~/.tokenpak/spend_guard.db"):
        self.path = _db_path(audit_db_path)

    # -- store -------------------------------------------------------------
    def store(
        self,
        *,
        session_id: str,
        body: bytes,
        headers: dict,
        target_url: str,
        provider: str,
        model: str,
        projected_tokens: int,
        projected_cost_usd: float,
        ttl_seconds: int = 600,
    ) -> PendingRequest:
        """Insert a new pending request and return it."""
        pending_id = "tpg_" + secrets.token_hex(8)
        now = time.time()
        expires_at = now + ttl_seconds
        request_hash = hash_request(body, model)
        blob = gzip.compress(body, compresslevel=3)
        # Credential headers never touch disk — replay re-applies live auth.
        safe_headers = redact_headers(headers)
        headers_json = json.dumps(safe_headers, default=str)

        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            # NB: expired rows are filtered out at SELECT time
            # (``expires_at > now`` predicate). Explicit cleanup is via
            # :meth:`expire_old` — keep store() free of side effects so
            # diagnostics on row counts stay deterministic.
            conn.execute(
                """INSERT INTO pending_requests
                       (pending_id, session_id, created_at, expires_at,
                        request_hash, provider, model, projected_tokens,
                        projected_cost_usd, raw_request_blob,
                        raw_request_headers, target_url, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (pending_id, session_id, now, expires_at, request_hash,
                 provider, model, projected_tokens, projected_cost_usd,
                 blob, headers_json, target_url),
            )
            conn.commit()
        finally:
            conn.close()

        return PendingRequest(
            pending_id=pending_id,
            session_id=session_id,
            created_at=now,
            expires_at=expires_at,
            request_hash=request_hash,
            provider=provider,
            model=model,
            projected_tokens=projected_tokens,
            projected_cost_usd=projected_cost_usd,
            raw_request_blob=body,           # uncompressed for caller convenience
            raw_request_headers=safe_headers,
            target_url=target_url,
            status="pending",
        )

    # -- lookup ------------------------------------------------------------
    def get_by_session(self, session_id: str) -> Optional[PendingRequest]:
        """Most recent pending request for the given session, or None."""
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            row = conn.execute(
                """SELECT * FROM pending_requests
                   WHERE session_id = ? AND status = 'pending' AND expires_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, time.time()),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_pending(row) if row else None

    def get_by_id(self, pending_id: str) -> Optional[PendingRequest]:
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM pending_requests WHERE pending_id = ?",
                (pending_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_pending(row) if row else None

    def recent_block_by_hash(
        self, request_hash: str, within_seconds: float = 30.0
    ) -> Optional[PendingRequest]:
        """Anti-loop: was this exact request_hash blocked recently?

        Returns the most-recent matching row regardless of status (so we can
        return the same block response without re-running the estimator).
        """
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            row = conn.execute(
                """SELECT * FROM pending_requests
                   WHERE request_hash = ? AND created_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (request_hash, time.time() - within_seconds),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_pending(row) if row else None

    # -- consume / discard -------------------------------------------------
    def consume(self, pending_id: str) -> Optional[PendingRequest]:
        """Return the pending request and mark it consumed.

        Atomic: only succeeds once. Subsequent calls return None.
        """
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM pending_requests "
                "WHERE pending_id = ? AND status = 'pending'",
                (pending_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE pending_requests SET status='consumed' WHERE pending_id = ?",
                (pending_id,),
            )
            conn.commit()
        finally:
            conn.close()
        return _row_to_pending(row)

    def discard(self, pending_id: str) -> bool:
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE pending_requests SET status='discarded' "
                "WHERE pending_id = ? AND status = 'pending'",
                (pending_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def expire_old(self) -> int:
        """Mark all pending rows past their expires_at as expired. Returns count."""
        conn = _connect(self.path)
        try:
            _ensure_schema(conn)
            cur = conn.execute(
                "UPDATE pending_requests SET status='expired' "
                "WHERE status='pending' AND expires_at < ?",
                (time.time(),),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def _row_to_pending(row: sqlite3.Row) -> PendingRequest:
    return PendingRequest(
        pending_id=row["pending_id"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        request_hash=row["request_hash"],
        provider=row["provider"],
        model=row["model"],
        projected_tokens=row["projected_tokens"],
        projected_cost_usd=row["projected_cost_usd"],
        raw_request_blob=gzip.decompress(row["raw_request_blob"]),
        raw_request_headers=json.loads(row["raw_request_headers"] or "{}"),
        target_url=row["target_url"],
        status=row["status"],
    )
