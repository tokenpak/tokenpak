"""
Usage Metering — License-keyed tracking of tokens, requests, and models.

Tracks usage per license_id for daily reporting to license server.
Enables usage-based pricing for Team/Enterprise tiers.
"""

import sqlite3
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
import requests
import logging

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    """A single usage record."""
    model: str
    input_tokens: int
    output_tokens: int
    saved_tokens: int
    request_type: str
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class UsageMeter:
    """
    Per-license usage collector.
    
    Tracks:
    - Tokens processed (input, output, saved)
    - Requests made
    - Models used
    
    Reports daily to license server.
    """

    def __init__(self, key_id: str, db_path: Optional[Path] = None):
        """
        Initialize meter for a license key.
        
        Args:
            key_id: License key ID to track
            db_path: Path to SQLite database (default: ~/.tokenpak/usage.db)
        """
        self.key_id = key_id
        
        if db_path is None:
            db_path = Path.home() / ".tokenpak" / "usage.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        """Create SQLite schema if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    saved_tokens INTEGER DEFAULT 0,
                    request_type TEXT,
                    reported BOOLEAN DEFAULT 0
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_key_id ON usage(key_id);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON usage(timestamp);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reported ON usage(reported);
            """)
            conn.commit()

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        saved_tokens: int,
        request_type: str,
    ) -> None:
        """
        Record a single request's usage.
        
        Called after each request completes. Non-blocking.
        
        Args:
            model: Model name (e.g., "claude-sonnet")
            input_tokens: Input tokens processed
            output_tokens: Output tokens generated
            saved_tokens: Tokens saved by compression
            request_type: Type of request (e.g., "chat", "completion")
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        def _insert():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO usage
                    (key_id, timestamp, model, input_tokens, output_tokens, saved_tokens, request_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.key_id,
                        timestamp,
                        model,
                        input_tokens,
                        output_tokens,
                        saved_tokens,
                        request_type,
                    ),
                )
                conn.commit()
        
        # Insert asynchronously in background thread to avoid blocking
        thread = threading.Thread(target=_insert, daemon=True)
        thread.start()

    def get_daily_summary(self, date: str) -> Dict[str, Any]:
        """
        Aggregate usage for a given date (YYYY-MM-DD).
        
        Args:
            date: Date string in YYYY-MM-DD format
        
        Returns:
            Dictionary with aggregated usage:
            {
                "date": "2026-03-22",
                "key_id": "test-key",
                "total_requests": 42,
                "total_input_tokens": 50000,
                "total_output_tokens": 10000,
                "total_saved_tokens": 5000,
                "by_model": {
                    "claude-sonnet": {
                        "requests": 30,
                        "input_tokens": 40000,
                        "output_tokens": 8000,
                        "saved_tokens": 4000
                    },
                    "claude-opus": {
                        "requests": 12,
                        "input_tokens": 10000,
                        "output_tokens": 2000,
                        "saved_tokens": 1000
                    }
                },
                "by_type": {
                    "chat": {"requests": 35, "input_tokens": 45000, ...},
                    "completion": {"requests": 7, "input_tokens": 5000, ...}
                }
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get all records for the day
            cursor = conn.execute(
                """
                SELECT * FROM usage
                WHERE key_id = ? AND DATE(timestamp) = ?
                """,
                (self.key_id, date),
            )
            records = cursor.fetchall()
        
        if not records:
            return {
                "date": date,
                "key_id": self.key_id,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_saved_tokens": 0,
                "by_model": {},
                "by_type": {},
            }
        
        # Aggregate by model
        by_model = {}
        by_type = {}
        total_input = 0
        total_output = 0
        total_saved = 0
        
        for row in records:
            model = row["model"] or "unknown"
            request_type = row["request_type"] or "unknown"
            input_tokens = row["input_tokens"] or 0
            output_tokens = row["output_tokens"] or 0
            saved_tokens = row["saved_tokens"] or 0
            
            # By model
            if model not in by_model:
                by_model[model] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "saved_tokens": 0,
                }
            by_model[model]["requests"] += 1
            by_model[model]["input_tokens"] += input_tokens
            by_model[model]["output_tokens"] += output_tokens
            by_model[model]["saved_tokens"] += saved_tokens
            
            # By type
            if request_type not in by_type:
                by_type[request_type] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "saved_tokens": 0,
                }
            by_type[request_type]["requests"] += 1
            by_type[request_type]["input_tokens"] += input_tokens
            by_type[request_type]["output_tokens"] += output_tokens
            by_type[request_type]["saved_tokens"] += saved_tokens
            
            # Totals
            total_input += input_tokens
            total_output += output_tokens
            total_saved += saved_tokens
        
        return {
            "date": date,
            "key_id": self.key_id,
            "total_requests": len(records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_saved_tokens": total_saved,
            "by_model": by_model,
            "by_type": by_type,
        }

    def report_to_server(self, server_url: str, timeout: int = 10) -> bool:
        """
        Upload unreported usage to license server.
        
        Batches all unreported rows and sends as:
        POST {server_url}/usage
        {
            "key_id": "...",
            "usage": [
                {"date": "2026-03-22", "summary": {...}},
                ...
            ]
        }
        
        On success, marks rows as reported.
        On failure, returns False (retried on next call).
        
        Args:
            server_url: Base URL of license server
            timeout: Request timeout in seconds
        
        Returns:
            True if upload successful (or no data to report)
            False if network error or server error
        """
        # Get all unreported rows, grouped by date
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT DATE(timestamp) as date FROM usage
                WHERE key_id = ? AND reported = 0
                ORDER BY date
                """,
                (self.key_id,),
            )
            unreported_dates = [row[0] for row in cursor.fetchall()]
        
        if not unreported_dates:
            logger.debug(f"No unreported usage for {self.key_id}")
            return True
        
        # Build payload
        usage_by_date = []
        for date in unreported_dates:
            summary = self.get_daily_summary(date)
            usage_by_date.append(summary)
        
        payload = {
            "key_id": self.key_id,
            "usage": usage_by_date,
        }
        
        # Send to server
        try:
            endpoint = server_url.rstrip("/") + "/usage"
            response = requests.post(
                endpoint,
                json=payload,
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            
            # Mark as reported
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE usage SET reported = 1
                    WHERE key_id = ? AND DATE(timestamp) IN ({})
                    """.format(",".join("?" * len(unreported_dates))),
                    [self.key_id] + unreported_dates,
                )
                conn.commit()
            
            logger.info(
                f"Reported usage for {self.key_id}: {len(unreported_dates)} dates"
            )
            return True
        
        except requests.RequestException as e:
            logger.warning(f"Failed to report usage to {server_url}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error reporting usage: {e}")
            return False

    def cleanup_old_data(self, days: int = 90) -> int:
        """
        Delete usage data older than N days (default 90).
        
        Returns: Number of rows deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM usage
                WHERE key_id = ? AND DATE(timestamp) < DATE('now', '-' || ? || ' days')
                """,
                (self.key_id, days),
            )
            conn.commit()
            return cursor.rowcount


class UsageMeterManager:
    """
    Manages multiple UsageMeter instances (one per license key).
    
    Thread-safe singleton for use in proxy.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_manager()
        return cls._instance
    
    def _init_manager(self):
        """Initialize manager state."""
        self._meters: Dict[str, UsageMeter] = {}
        self._lock = threading.Lock()
    
    def get_meter(self, key_id: str) -> UsageMeter:
        """Get or create meter for key_id."""
        if key_id not in self._meters:
            with self._lock:
                if key_id not in self._meters:
                    self._meters[key_id] = UsageMeter(key_id)
        return self._meters[key_id]
    
    def record_usage(
        self,
        key_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        saved_tokens: int,
        request_type: str = "chat",
    ) -> None:
        """Record usage for a license key."""
        meter = self.get_meter(key_id)
        meter.record(model, input_tokens, output_tokens, saved_tokens, request_type)
    
    def get_daily_summary(self, key_id: str, date: str) -> Dict[str, Any]:
        """Get daily summary for a license key."""
        meter = self.get_meter(key_id)
        return meter.get_daily_summary(date)
    
    def report_all(self, server_url: str) -> Dict[str, bool]:
        """Report all pending usage for all meters. Returns {key_id: success}."""
        results = {}
        for key_id, meter in self._meters.items():
            results[key_id] = meter.report_to_server(server_url)
        return results
