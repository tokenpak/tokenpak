"""
TokenPak Telemetry - Retention & Pruning

Automatic cleanup of old events and rollups based on retention policy.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RetentionConfig:
    """Retention policy configuration."""

    events_days: int = 90  # Keep events for 90 days
    rollups_days: int = 365  # Keep rollups for 1 year
    auto_prune: bool = True  # Run auto-prune
    prune_schedule: str = "0 2 * * *"  # 2 AM daily (cron format)


@dataclass
class PruneResult:
    """Result of a prune operation."""

    events_deleted: int = 0
    rollups_deleted: int = 0
    duration_seconds: float = 0.0
    db_size_before_bytes: int = 0
    db_size_after_bytes: int = 0
    success: bool = False


class PruneJob:
    """Handles retention and pruning."""

    def __init__(self, db_path: str, config: RetentionConfig):
        self.db_path = db_path
        self.config = config

    def prune_old_events(self, older_than_days: int) -> int:
        """
        Delete events older than N days.

        Returns:
            Count of deleted events
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Calculate cutoff date
            cutoff = datetime.utcnow() - timedelta(days=older_than_days)
            cutoff_str = cutoff.isoformat()

            # Delete events older than cutoff
            cursor.execute("DELETE FROM events WHERE created_at < ?", (cutoff_str,))

            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()

            return deleted_count
        except Exception as e:
            print(f"Error pruning events: {e}")
            return 0

    def prune_old_rollups(self, older_than_days: int) -> int:
        """
        Delete rollups older than N days.

        Returns:
            Count of deleted rollups
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Calculate cutoff date
            cutoff = datetime.utcnow() - timedelta(days=older_than_days)
            cutoff_str = cutoff.isoformat()

            # Delete rollups older than cutoff
            cursor.execute("DELETE FROM rollups WHERE created_at < ?", (cutoff_str,))

            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()

            return deleted_count
        except Exception as e:
            print(f"Error pruning rollups: {e}")
            return 0

    def vacuum_database(self) -> bool:
        """
        Run VACUUM to reclaim disk space.

        Returns:
            Success flag
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("VACUUM")
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error vacuuming database: {e}")
            return False

    def run_prune(self) -> PruneResult:
        """
        Run full prune operation: delete old events/rollups, then vacuum.

        Returns:
            PruneResult with details
        """
        import time

        start_time = time.time()
        result = PruneResult()

        try:
            # Capture size before
            result.db_size_before_bytes = os.path.getsize(self.db_path)

            # Prune events
            result.events_deleted = self.prune_old_events(self.config.events_days)

            # Prune rollups (keep longer)
            result.rollups_deleted = self.prune_old_rollups(self.config.rollups_days)

            # Vacuum
            self.vacuum_database()

            # Capture size after
            result.db_size_after_bytes = os.path.getsize(self.db_path)
            result.duration_seconds = time.time() - start_time
            result.success = True

            return result
        except Exception as e:
            result.duration_seconds = time.time() - start_time
            print(f"Error during prune: {e}")
            return result


def load_retention_config(config_path: str) -> RetentionConfig:
    """Load retention config from JSON file."""
    try:
        with open(config_path) as f:
            data = json.load(f)
            retention = data.get("retention", {})
            return RetentionConfig(
                events_days=retention.get("events_days", 90),
                rollups_days=retention.get("rollups_days", 365),
                auto_prune=retention.get("auto_prune", True),
                prune_schedule=retention.get("prune_schedule", "0 2 * * *"),
            )
    except Exception as e:
        print(f"Error loading retention config: {e}")
        return RetentionConfig()  # Use defaults
