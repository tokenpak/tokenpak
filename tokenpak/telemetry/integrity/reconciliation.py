"""
TokenPak Reconciliation System

Track proxy-reported vs billed tokens, flag mismatches.
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ReconciliationRecord:
    """Reconciliation record for period + provider + model."""

    period_start: str
    provider: str
    model: str
    proxy_tokens: int = 0
    billed_tokens: int = 0
    difference: int = 0
    difference_pct: float = 0.0
    status: str = "pending"  # matched, mismatch, pending
    created_at: str = None  # type: ignore[assignment]


class ReconciliationManager:
    """Manages reconciliation of proxy vs billed tokens."""

    # Mismatch threshold
    MISMATCH_THRESHOLD_PCT = 5.0

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self):
        """Create reconciliation table if not exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tp_reconciliation (
                id INTEGER PRIMARY KEY,
                period_start TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                proxy_tokens INTEGER DEFAULT 0,
                billed_tokens INTEGER DEFAULT 0,
                difference INTEGER DEFAULT 0,
                difference_pct REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(period_start, provider, model)
            )
        """)

        conn.commit()
        conn.close()

    def import_billing_data(self, records: List[Dict]) -> int:
        """
        Import billing data for reconciliation.

        Args:
            records: List of {period_start, provider, model, billed_tokens}

        Returns:
            Count of records imported
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        imported = 0
        for record in records:
            try:
                period_start = record["period_start"]
                provider = record["provider"]
                model = record["model"]
                billed_tokens = record["billed_tokens"]

                # Get proxy tokens for same period
                cursor.execute(
                    """
                    SELECT SUM(final_input_tokens) FROM events
                    WHERE provider = ? AND model = ?
                    AND DATE(created_at) >= DATE(?)
                    AND DATE(created_at) < DATE(?, '+1 day')
                """,
                    (provider, model, period_start, period_start),
                )

                result = cursor.fetchone()
                proxy_tokens = result[0] or 0

                # Calculate difference
                difference = abs(proxy_tokens - billed_tokens)
                difference_pct = (difference / billed_tokens * 100) if billed_tokens > 0 else 0.0

                # Determine status
                status = "matched" if difference_pct <= self.MISMATCH_THRESHOLD_PCT else "mismatch"

                # Insert or update
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO tp_reconciliation
                    (period_start, provider, model, proxy_tokens, billed_tokens,
                     difference, difference_pct, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        period_start,
                        provider,
                        model,
                        proxy_tokens,
                        billed_tokens,
                        difference,
                        difference_pct,
                        status,
                    ),
                )

                imported += 1
            except Exception as e:
                print(f"Error importing billing record: {e}")

        conn.commit()
        conn.close()

        return imported

    def get_reconciliation_status(self) -> Dict:
        """
        Get reconciliation rate and summary stats.

        Returns:
            {
                'total_records': int,
                'matched_records': int,
                'mismatch_records': int,
                'reconciliation_rate': float,
                'total_difference_tokens': int,
                'avg_difference_pct': float
            }
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM tp_reconciliation")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM tp_reconciliation WHERE status = ?", ("matched",))
        matched = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM tp_reconciliation WHERE status = ?", ("mismatch",))
        mismatches = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(difference), AVG(difference_pct) FROM tp_reconciliation")
        result = cursor.fetchone()
        total_difference = result[0] or 0
        avg_difference_pct = result[1] or 0.0

        reconciliation_rate = (matched / total * 100) if total > 0 else 0.0

        conn.close()

        return {
            "total_records": total,
            "matched_records": matched,
            "mismatch_records": mismatches,
            "reconciliation_rate": round(reconciliation_rate, 1),
            "total_difference_tokens": total_difference,
            "avg_difference_pct": round(avg_difference_pct, 2),
            "target": 95.0,
            "status": "ok" if reconciliation_rate >= 95.0 else "warning",
        }

    def get_mismatches(self, limit: int = 10) -> List[Dict]:
        """Get recent mismatches."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT period_start, provider, model, proxy_tokens, billed_tokens,
                   difference, difference_pct
            FROM tp_reconciliation
            WHERE status = 'mismatch'
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (limit,),
        )

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "period_start": r[0],
                "provider": r[1],
                "model": r[2],
                "proxy_tokens": r[3],
                "billed_tokens": r[4],
                "difference": r[5],
                "difference_pct": r[6],
            }
            for r in rows
        ]
