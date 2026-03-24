"""
Error Telemetry Logger for TokenPak

Captures exceptions, failures, and warnings with context for post-mortem analysis.
Writes to append-only JSON Lines format with automatic log rotation.

This module provides a comprehensive error tracking system for production TokenPak
deployments, capturing exceptions with contextual metadata for post-mortem analysis.
"""

import os
import json
import logging
import threading
import gzip
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import traceback
from dataclasses import dataclass, asdict
from functools import wraps

# Get logger
logger = logging.getLogger(__name__)

__all__ = [
    "ErrorLogger",
    "ErrorContext",
    "get_error_logger",
    "log_exception",
]


@dataclass
class ErrorContext:
    """Structured error context for logging"""
    timestamp: str
    request_id: str
    error_type: str
    message: str
    stack_trace: str
    context: Dict[str, Any]  # model, provider, input_size, cost_estimate, etc.


class ErrorLogger:
    """Thread-safe error telemetry logger for TokenPak proxy"""

    def __init__(self, log_dir: Optional[str] = None):
        """
        Initialize error logger.

        Args:
            log_dir: Directory to store error logs. Defaults to ~/.tokenpak/logs/
        """
        if log_dir is None:
            log_dir = os.path.expanduser("~/.tokenpak/logs")

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.log_dir / "archive"
        self.archive_dir.mkdir(exist_ok=True)

        self._lock = threading.Lock()
        self._prometheus_metrics = {}

    def log_error(
        self,
        request_id: str,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an exception with context.

        Args:
            request_id: Unique identifier for the request
            error: The exception that occurred
            context: Dict with optional fields:
                - model: Model name (e.g., "gpt-4")
                - provider: Provider name (e.g., "openai")
                - input_size: Input token count
                - output_size: Output token count (if applicable)
                - cost_estimate: Estimated cost in dollars
                - duration_ms: Request duration in milliseconds
        """
        if context is None:
            context = {}

        error_entry = ErrorContext(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            request_id=request_id,
            error_type=type(error).__name__,
            message=str(error),
            stack_trace=traceback.format_exc(),
            context=context,
        )

        with self._lock:
            self._write_error_log(error_entry)
            self._update_prometheus_metrics(error_entry)
            self._check_rotation()

    def _write_error_log(self, error_entry: ErrorContext) -> None:
        """Write error to JSON Lines log file"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self.log_dir / f"errors-{today}.jsonl"

        try:
            with open(log_file, "a") as f:
                json.dump(asdict(error_entry), f)
                f.write("\n")
        except Exception as e:
            logger.error(f"Failed to write error log: {e}")

    def _update_prometheus_metrics(self, error_entry: ErrorContext) -> None:
        """Track error count by type for Prometheus"""
        error_type = error_entry.error_type
        if error_type not in self._prometheus_metrics:
            self._prometheus_metrics[error_type] = 0
        self._prometheus_metrics[error_type] += 1

    def _check_rotation(self) -> None:
        """Check if log rotation is needed (keep last 7 days)"""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=7)).date()

        for log_file in self.log_dir.glob("errors-*.jsonl"):
            # Parse date from filename: errors-YYYY-MM-DD.jsonl
            try:
                date_str = log_file.stem.replace("errors-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").date()

                if file_date < cutoff_date:
                    self._archive_log(log_file)
            except ValueError:
                # Skip files with unexpected names
                continue

    def _archive_log(self, log_file: Path) -> None:
        """Archive old log file (gzip + move to archive/)"""
        try:
            archive_path = self.archive_dir / f"{log_file.name}.gz"
            with open(log_file, "rb") as f_in:
                with gzip.open(archive_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            log_file.unlink()  # Delete original after successful gzip
            logger.info(f"Archived log: {log_file.name}")
        except Exception as e:
            logger.error(f"Failed to archive log {log_file.name}: {e}")

    def get_error_summary(
        self, days: int = 1, error_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get error summary for reporting.

        Args:
            days: Number of days to look back (default: 1)
            error_type: Filter by specific error type (optional)

        Returns:
            Dict with error counts, types, providers, etc.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        error_counts = {}
        provider_counts = {}
        total_errors = 0

        cutoff_date = cutoff.date()
        for log_file in self.log_dir.glob("errors-*.jsonl"):
            try:
                date_str = log_file.stem.replace("errors-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").date()

                if file_date < cutoff_date:
                    continue

                with open(log_file, "r") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)

                            # Apply filters
                            if error_type and entry["error_type"] != error_type:
                                continue

                            # Timestamp filter
                            entry_time = datetime.fromisoformat(
                                entry["timestamp"].replace("Z", "+00:00")
                            )
                            if entry_time < cutoff:
                                continue

                            # Count by error type
                            err_type = entry["error_type"]
                            error_counts[err_type] = error_counts.get(err_type, 0) + 1

                            # Count by provider
                            provider = entry.get("context", {}).get("provider", "unknown")
                            provider_counts[provider] = provider_counts.get(provider, 0) + 1

                            total_errors += 1
                        except json.JSONDecodeError:
                            logger.warning(f"Malformed log line in {log_file}")
            except ValueError:
                continue

        return {
            "period_days": days,
            "total_errors": total_errors,
            "by_error_type": error_counts,
            "by_provider": provider_counts,
        }

    def get_metrics(self) -> Dict[str, int]:
        """Get current Prometheus-style metrics"""
        return self._prometheus_metrics.copy()


# Global logger instance
_error_logger = None
_logger_lock = threading.Lock()


def get_error_logger() -> ErrorLogger:
    """Get singleton error logger instance"""
    global _error_logger
    if _error_logger is None:
        with _logger_lock:
            if _error_logger is None:
                _error_logger = ErrorLogger()
    return _error_logger


def log_exception(request_id: str, context: Optional[Dict[str, Any]] = None):
    """
    Decorator to automatically log exceptions.

    Args:
        request_id: Unique request identifier
        context: Optional dict with request context

    Example:
        @log_exception("req-123", {"model": "gpt-4", "provider": "openai"})
        def call_llm():
            return openai.ChatCompletion.create(...)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                get_error_logger().log_error(request_id, e, context)
                raise
        return wrapper
    return decorator
