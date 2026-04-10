# SPDX-License-Identifier: Apache-2.0
"""Routing Ledger for TokenPak Shadow Mode.

Logs every LLM transaction to SQLite with WAL mode for concurrent access.
No agentic action — observe only.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .complexity import score_complexity

DEFAULT_LEDGER_PATH = ".tokenpak/routing_ledger.db"

# WAL checkpoint interval (pages)
_WAL_AUTOCHECKPOINT = 1000


class RoutingLedger:
    """
    Thread-safe SQLite ledger for LLM transaction logging.
    Uses WAL mode for concurrent readers + single writer.
    """

    def __init__(self, db_path: str = DEFAULT_LEDGER_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._connect()
            # Enable WAL mode for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA wal_autocheckpoint={_WAL_AUTOCHECKPOINT}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        TEXT    NOT NULL,
                    model_used       TEXT    NOT NULL,
                    task_type        TEXT    NOT NULL DEFAULT 'UNKNOWN',
                    complexity_score REAL    NOT NULL DEFAULT 0.0,
                    context_tokens   INTEGER NOT NULL DEFAULT 0,
                    context_weight   REAL    NOT NULL DEFAULT 0.0,
                    response_tokens  INTEGER NOT NULL DEFAULT 0,
                    accepted         INTEGER,          -- NULL=unreviewed, 1=accepted, 0=rejected
                    rejection_reason TEXT,
                    latency_ms       REAL    NOT NULL DEFAULT 0.0,
                    query_preview    TEXT,             -- first 200 chars of query
                    routing_action   TEXT    DEFAULT 'passthrough'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts    ON transactions(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON transactions(model_used)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type  ON transactions(task_type)")
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_transaction(
        self,
        model: str,
        query: str,
        context_blocks: List[str],
        response: str,
        accepted: Optional[bool] = None,
        rejection_reason: Optional[str] = None,
        latency_ms: float = 0.0,
        context_tokens: int = 0,
        response_tokens: int = 0,
        routing_action: str = "passthrough",
    ) -> int:
        """
        Log a single LLM transaction.

        Args:
            model:            Model name (e.g. "claude-sonnet-4-6").
            query:            The user's query / prompt.
            context_blocks:   Block content strings in context (for complexity scoring).
            response:         The LLM response text.
            accepted:         True=accepted, False=rejected, None=unreviewed.
            rejection_reason: Optional reason for rejection.
            latency_ms:       Total response latency in milliseconds.
            context_tokens:   Token count of context sent.
            response_tokens:  Token count of response received.
            routing_action:   One of passthrough / downgrade / upgrade.

        Returns:
            Row ID of inserted transaction.
        """
        complexity_score, task_type = score_complexity(query, context_blocks)
        context_weight = self._compute_context_weight(context_tokens, response_tokens)
        accepted_int = None if accepted is None else (1 if accepted else 0)

        ts = datetime.now(timezone.utc).isoformat()
        query_preview = query[:200] if query else ""

        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (timestamp, model_used, task_type, complexity_score,
                     context_tokens, context_weight, response_tokens,
                     accepted, rejection_reason, latency_ms,
                     query_preview, routing_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    ts,
                    model,
                    task_type.value,
                    complexity_score,
                    context_tokens,
                    context_weight,
                    response_tokens,
                    accepted_int,
                    rejection_reason,
                    latency_ms,
                    query_preview,
                    routing_action,
                ),
            )
            row_id = cur.lastrowid
            conn.commit()
            conn.close()
        assert row_id is not None
        return row_id

    def record_outcome(
        self,
        transaction_id: int,
        accepted: bool,
        rejection_reason: Optional[str] = None,
    ) -> bool:
        """
        Update the acceptance status of an existing transaction.
        Used when feedback arrives after the initial log.

        Returns True if the row was found and updated.
        """
        accepted_int = 1 if accepted else 0
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                UPDATE transactions
                SET accepted = ?, rejection_reason = ?
                WHERE id = ?
            """,
                (accepted_int, rejection_reason, transaction_id),
            )
            updated = cur.rowcount > 0
            conn.commit()
            conn.close()
        return updated

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_transaction(self, transaction_id: int) -> Optional[dict]:
        """Fetch a single transaction by ID."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_recent(self, limit: int = 100) -> List[dict]:
        """Return the most recent N transactions."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Return aggregate statistics from the ledger."""
        conn = self._connect()
        row = conn.execute("""
            SELECT
                COUNT(*)                                AS total,
                SUM(CASE WHEN accepted=1 THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN accepted=0 THEN 1 ELSE 0 END) AS rejected,
                SUM(CASE WHEN accepted IS NULL THEN 1 ELSE 0 END) AS unreviewed,
                AVG(complexity_score)                   AS avg_complexity,
                AVG(latency_ms)                         AS avg_latency_ms,
                AVG(context_tokens)                     AS avg_context_tokens
            FROM transactions
        """).fetchone()
        model_counts = conn.execute("""
            SELECT model_used, COUNT(*) AS n FROM transactions GROUP BY model_used
        """).fetchall()
        type_counts = conn.execute("""
            SELECT task_type, COUNT(*) AS n FROM transactions GROUP BY task_type
        """).fetchall()
        conn.close()

        stats = dict(row) if row else {}
        stats["by_model"] = {r["model_used"]: r["n"] for r in model_counts}
        stats["by_task_type"] = {r["task_type"]: r["n"] for r in type_counts}
        return stats

    def sample_count(self, model: str, task_type: str) -> int:
        """Return number of transactions for (model, task_type) with known outcome."""
        conn = self._connect()
        row = conn.execute(
            """
            SELECT COUNT(*) FROM transactions
            WHERE model_used = ? AND task_type = ? AND accepted IS NOT NULL
        """,
            (model, task_type),
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def acceptance_rate(self, model: str, task_type: str) -> float:
        """Return acceptance rate for (model, task_type). Returns 0.0 if no data."""
        conn = self._connect()
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN accepted=1 THEN 1 ELSE 0 END) AS wins,
                COUNT(*) AS total
            FROM transactions
            WHERE model_used = ? AND task_type = ? AND accepted IS NOT NULL
        """,
            (model, task_type),
        ).fetchone()
        conn.close()
        if not row or row["total"] == 0:
            return 0.0
        return row["wins"] / row["total"]

    def wal_mode_active(self) -> bool:
        """Return True if WAL journal mode is active."""
        conn = self._connect()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        return row[0].lower() == "wal" if row else False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_context_weight(context_tokens: int, response_tokens: int) -> float:
        """
        Context weight = context_tokens / (context_tokens + response_tokens).
        Measure of how much of the token budget is context vs response.
        """
        total = context_tokens + response_tokens
        if total == 0:
            return 0.0
        return round(context_tokens / total, 4)
