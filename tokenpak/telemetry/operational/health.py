"""
TokenPak Telemetry - Health Checks

Detailed health check endpoint for operational monitoring.
"""

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class HealthStatus:
    """Health status response."""

    status: str  # healthy, degraded, unhealthy
    version: str
    uptime_seconds: int
    checks: Dict[str, str]  # component -> ok/error/degraded
    stats: Dict[str, Any]  # stats snapshot


class HealthChecker:
    """Performs health checks on components."""

    def __init__(self, db_path: str, version: str = "0.1.0"):
        self.db_path = db_path
        self.version = version
        self.start_time = time.time()

    def check_database(self) -> tuple[str, Optional[str]]:
        """Check database connectivity and health."""
        try:
            if not os.path.exists(self.db_path):
                return "error", f"Database not found: {self.db_path}"

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check tables exist
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events', 'rollups')"
            )
            tables = cursor.fetchall()

            if len(tables) < 1:
                return "error", "Missing required tables"

            # Quick integrity check
            cursor.execute("SELECT COUNT(*) FROM events LIMIT 1")
            cursor.fetchone()

            conn.close()
            return "ok", None
        except Exception as e:
            return "error", str(e)

    def check_pricing_catalog(self) -> tuple[str, Optional[str]]:
        """Check pricing catalog (if exists)."""
        try:
            # For now, assume ok if database is ok
            return "ok", None
        except Exception as e:
            return "error", str(e)

    def check_rollup_job(self) -> tuple[str, Optional[str]]:
        """Check rollup job status."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT MAX(created_at) FROM rollups")
            last_rollup = cursor.fetchone()[0]

            if not last_rollup:
                return "ok", None  # No rollups yet

            # Check if last rollup is recent (within 24 hours)
            last_rollup_time = datetime.fromisoformat(last_rollup).timestamp()
            hours_since = (time.time() - last_rollup_time) / 3600

            if hours_since > 25:
                return "degraded", f"Last rollup {hours_since:.1f} hours ago"

            conn.close()
            return "ok", None
        except Exception as e:
            return "error", str(e)

    def get_stats(self) -> Dict[str, Any]:
        """Get operational statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Total events
            cursor.execute("SELECT COUNT(*) FROM events")
            events_total = cursor.fetchone()[0]

            # Events today
            cursor.execute("SELECT COUNT(*) FROM events WHERE DATE(created_at) = DATE('now')")
            events_today = cursor.fetchone()[0]

            # Last ingest
            cursor.execute("SELECT MAX(created_at) FROM events")
            last_ingest = cursor.fetchone()[0]

            # DB size
            db_size = os.path.getsize(self.db_path)
            db_size_mb = db_size / (1024 * 1024)

            # Last rollup
            cursor.execute("SELECT MAX(created_at) FROM rollups")
            rollup_last_run = cursor.fetchone()[0]

            conn.close()

            return {
                "events_total": events_total,
                "events_today": events_today,
                "last_ingest_at": last_ingest,
                "db_size_bytes": db_size,
                "db_size_mb": round(db_size_mb, 1),
                "rollup_last_run": rollup_last_run,
            }
        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> HealthStatus:
        """Run full health check."""
        checks = {}
        has_error = False
        has_degraded = False

        # Database check
        status, error = self.check_database()
        checks["database"] = status
        if status == "error":
            has_error = True
        elif status == "degraded":
            has_degraded = True

        # Pricing catalog check
        status, error = self.check_pricing_catalog()
        checks["pricing_catalog"] = status
        if status == "error":
            has_error = True
        elif status == "degraded":
            has_degraded = True

        # Rollup job check
        status, error = self.check_rollup_job()
        checks["rollup_job"] = status
        if status == "error":
            has_error = True
        elif status == "degraded":
            has_degraded = True

        # Overall status
        if has_error:
            overall_status = "unhealthy"
        elif has_degraded:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return HealthStatus(
            status=overall_status,
            version=self.version,
            uptime_seconds=int(time.time() - self.start_time),
            checks=checks,
            stats=self.get_stats(),
        )
