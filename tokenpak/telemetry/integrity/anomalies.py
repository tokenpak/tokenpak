"""
TokenPak Anomaly Detection

Automatic detection of spikes and anomalies in token/cost metrics.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional


@dataclass
class Anomaly:
    """Detected anomaly."""

    anomaly_type: str  # token_spike, cost_spike, retry_surge, error_surge
    severity: str  # warning, critical
    description: str
    detected_at: str
    event_ids: List[str]
    value: float = 0.0
    baseline: float = 0.0
    threshold: float = 0.0


class AnomalyDetector:
    """Detects anomalies in telemetry data."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self):
        """Create anomalies table if not exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tp_anomalies (
                id INTEGER PRIMARY KEY,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                anomaly_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT,
                event_ids TEXT,
                acknowledged BOOLEAN DEFAULT FALSE,
                acknowledged_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def detect_token_spikes(
        self, model: str, current_tokens: int, baseline_days: int = 7
    ) -> Optional[Anomaly]:
        """
        Detect >10× token usage vs baseline.

        Args:
            model: Model to check
            current_tokens: Current token count
            baseline_days: Days to use for baseline

        Returns:
            Anomaly if detected, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get baseline (7-day average)
        (datetime.utcnow() - timedelta(days=baseline_days)).isoformat()

        cursor.execute(
            """
            SELECT AVG(final_input_tokens) FROM events
            WHERE model = ? AND created_at < ?
            AND DATE(created_at) >= DATE(?, '-' || ? || ' days')
        """,
            (model, datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), baseline_days),
        )

        result = cursor.fetchone()
        baseline = result[0] or 0

        conn.close()

        # Check if >10× baseline
        threshold = baseline * 10
        if current_tokens > threshold and baseline > 0:
            return Anomaly(
                anomaly_type="token_spike",
                severity="warning" if current_tokens < threshold * 2 else "critical",
                description=f"Token spike: {current_tokens} vs baseline {baseline:.0f}",
                detected_at=datetime.utcnow().isoformat(),
                event_ids=[],
                value=current_tokens,
                baseline=baseline,
                threshold=threshold,
            )

        return None

    def detect_cost_spikes(self, current_cost: float, baseline_days: int = 1) -> Optional[Anomaly]:
        """
        Detect >10× daily cost spike.

        Args:
            current_cost: Current daily cost
            baseline_days: Days to use for baseline (default: yesterday)

        Returns:
            Anomaly if detected, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get baseline (yesterday's average or last N days)
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()

        cursor.execute(
            """
            SELECT AVG(actual_cost) FROM events
            WHERE DATE(created_at) = ?
        """,
            (yesterday,),
        )

        result = cursor.fetchone()
        baseline = result[0] or 0

        conn.close()

        threshold = baseline * 10
        if current_cost > threshold and baseline > 0:
            return Anomaly(
                anomaly_type="cost_spike",
                severity="warning" if current_cost < threshold * 2 else "critical",
                description=f"Cost spike: ${current_cost:.2f} vs baseline ${baseline:.2f}",
                detected_at=datetime.utcnow().isoformat(),
                event_ids=[],
                value=current_cost,
                baseline=baseline,
                threshold=threshold,
            )

        return None

    def detect_retry_surge(
        self, time_window_minutes: int = 60, threshold_pct: float = 20.0
    ) -> Optional[Anomaly]:
        """
        Detect retry rate >20% in window.

        Args:
            time_window_minutes: Time window to check
            threshold_pct: Retry rate threshold

        Returns:
            Anomaly if detected, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        start_time = (datetime.utcnow() - timedelta(minutes=time_window_minutes)).isoformat()

        cursor.execute(
            """
            SELECT COUNT(*) FROM events WHERE created_at > ? AND retry_count > 0
        """,
            (start_time,),
        )

        retried = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM events WHERE created_at > ?
        """,
            (start_time,),
        )

        total = cursor.fetchone()[0]
        conn.close()

        if total == 0:
            return None

        retry_rate = retried / total * 100

        if retry_rate > threshold_pct:
            return Anomaly(
                anomaly_type="retry_surge",
                severity="warning" if retry_rate < 30 else "critical",
                description=f"Retry surge: {retry_rate:.1f}% ({retried}/{total} events)",
                detected_at=datetime.utcnow().isoformat(),
                event_ids=[],
                value=retry_rate,
                baseline=0,
                threshold=threshold_pct,
            )

        return None

    def detect_error_surge(
        self, time_window_minutes: int = 60, threshold_pct: float = 10.0
    ) -> Optional[Anomaly]:
        """
        Detect error rate >10% in window.

        Args:
            time_window_minutes: Time window to check
            threshold_pct: Error rate threshold

        Returns:
            Anomaly if detected, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        start_time = (datetime.utcnow() - timedelta(minutes=time_window_minutes)).isoformat()

        cursor.execute(
            """
            SELECT COUNT(*) FROM events WHERE created_at > ? AND status = 'error'
        """,
            (start_time,),
        )

        errors = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM events WHERE created_at > ?
        """,
            (start_time,),
        )

        total = cursor.fetchone()[0]
        conn.close()

        if total == 0:
            return None

        error_rate = errors / total * 100

        if error_rate > threshold_pct:
            return Anomaly(
                anomaly_type="error_surge",
                severity="critical" if error_rate > 20 else "warning",
                description=f"Error surge: {error_rate:.1f}% ({errors}/{total} events)",
                detected_at=datetime.utcnow().isoformat(),
                event_ids=[],
                value=error_rate,
                baseline=0,
                threshold=threshold_pct,
            )

        return None

    def record_anomaly(self, anomaly: Anomaly) -> int | None:
        """Record detected anomaly in database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO tp_anomalies
            (detected_at, anomaly_type, severity, description, event_ids)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                anomaly.detected_at,
                anomaly.anomaly_type,
                anomaly.severity,
                anomaly.description,
                json.dumps(anomaly.event_ids),
            ),
        )

        anomaly_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return anomaly_id

    def get_recent_anomalies(self, since: str | None = None, limit: int = 50) -> List[Dict]:
        """Get recent anomalies."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if since:
            cursor.execute(
                """
                SELECT id, detected_at, anomaly_type, severity, description,
                       event_ids, acknowledged
                FROM tp_anomalies
                WHERE detected_at > ? AND acknowledged = FALSE
                ORDER BY detected_at DESC
                LIMIT ?
            """,
                (since, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, detected_at, anomaly_type, severity, description,
                       event_ids, acknowledged
                FROM tp_anomalies
                WHERE acknowledged = FALSE
                ORDER BY detected_at DESC
                LIMIT ?
            """,
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": r[0],
                "detected_at": r[1],
                "anomaly_type": r[2],
                "severity": r[3],
                "description": r[4],
                "event_ids": json.loads(r[5]) if r[5] else [],
                "acknowledged": bool(r[6]),
            }
            for r in rows
        ]

    def acknowledge_anomaly(self, anomaly_id: int) -> bool:
        """Mark anomaly as acknowledged."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE tp_anomalies
            SET acknowledged = TRUE, acknowledged_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """,
            (anomaly_id,),
        )

        conn.commit()
        success = cursor.rowcount > 0
        conn.close()

        return success
