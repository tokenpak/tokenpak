# SPDX-License-Identifier: Apache-2.0
"""Persistent context-percent approvals; other policy bands stay independent."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .contracts import RiskEstimate, TIPDirective


def scope_key(session: str, model: str, headers: dict[str, str]) -> str:
    normalized = {str(k).lower(): str(v).strip() for k, v in headers.items()}
    return hashlib.sha256(
        json.dumps(
            [
                session,
                model,
                normalized.get("x-tokenpak-fleet", ""),
                normalized.get("x-tokenpak-agent", ""),
            ]
        ).encode()
    ).hexdigest()


class ContextApprovals:
    """State in the existing guard DB, keyed by session/model/deployment/principal."""

    def __init__(self, path: Path):
        self.path = path
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS context_approvals (
                scope_key TEXT PRIMARY KEY, expires_at REAL,
                max_tokens INTEGER, remaining_cost REAL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS expired_approval_uses (
                scope_key TEXT NOT NULL, reply_hash TEXT NOT NULL,
                used_at REAL NOT NULL, PRIMARY KEY(scope_key, reply_hash))""")

    def _connect(self):
        # Explicit closing matters: sqlite connection context managers only
        # commit/rollback. Return a closing context through the helper below.
        return _connection(self.path)

    def create(self, key: str, tip: TIPDirective, risk: RiskEstimate) -> None:
        if tip.allow_scope not in ("15m", "session"):
            return
        expires = time.time() + 900 if tip.allow_scope == "15m" else None
        remaining = (
            None
            if tip.max_cost_usd is None
            else max(0.0, tip.max_cost_usd - risk.projected_cost_usd)
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO context_approvals VALUES (?, ?, ?, ?)",
                (key, expires, tip.max_tokens, remaining),
            )

    def revoke(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM context_approvals WHERE scope_key = ?", (key,))

    def redeem(self, key: str, risk: RiskEstimate) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT expires_at, max_tokens, remaining_cost FROM context_approvals WHERE scope_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return False
            expires, tokens, cost = row
            if (
                (expires is not None and time.time() >= expires)
                or (
                    tokens is not None
                    and risk.projected_input_tokens + risk.projected_output_tokens > tokens
                )
                or (cost is not None and risk.projected_cost_usd > cost)
            ):
                conn.execute("DELETE FROM context_approvals WHERE scope_key = ?", (key,))
                return False
            if cost is not None:
                conn.execute(
                    "UPDATE context_approvals SET remaining_cost = ? WHERE scope_key = ?",
                    (max(0.0, cost - risk.projected_cost_usd), key),
                )
            return True

    def reply_used(self, key: str, reply_hash: str) -> bool:
        with self._connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM expired_approval_uses WHERE scope_key = ? AND reply_hash = ?",
                    (key, reply_hash),
                ).fetchone()
                is not None
            )

    def remember_reply(self, key: str, reply_hash: str) -> None:
        """A rejected late reply cannot approve the replacement hold."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO expired_approval_uses VALUES (?, ?, ?)",
                (key, reply_hash, time.time()),
            )

    def consume_expired(self, key: str, pending_id: str, reply_hash: str) -> bool:
        """Consume an expired hold and its approval together, once across processes."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM expired_approval_uses WHERE scope_key = ? AND reply_hash = ?",
                (key, reply_hash),
            ).fetchone():
                return False
            changed = conn.execute(
                "UPDATE pending_requests SET status='consumed' WHERE pending_id = ? AND status IN ('pending', 'expired') AND expires_at <= ?",
                (pending_id, time.time()),
            ).rowcount
            if changed != 1:
                return False
            conn.execute(
                "INSERT INTO expired_approval_uses VALUES (?, ?, ?)", (key, reply_hash, time.time())
            )
            return True


@contextmanager
def _connection(path: Path):
    conn = sqlite3.connect(str(path), timeout=20)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
