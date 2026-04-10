# SPDX-License-Identifier: Apache-2.0
"""Structured logging configuration for TokenPak.

Usage::

    # In your entrypoint or __init__.py:
    from tokenpak.logging_config import configure_logging
    configure_logging()

    # Then use standard logging:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Proxy started", extra={"port": 8766})

Environment variables:

    TPK_LOG_LEVEL   Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                    Default: INFO.
    TPK_LOG_FORMAT  Output format: "json" for structured JSON, "text" for human-readable.
                    Default: "text".
    TPK_LOG_FILE    Optional path to a log file (in addition to stderr).

"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from typing import Any

__all__ = ["configure_logging", "get_logger", "TPK_LOGGER_NAME"]

TPK_LOGGER_NAME = "tokenpak"

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """Emit log records as newline-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include any extra fields attached via logger.info(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in _STDLIB_LOG_ATTRS and not key.startswith("_"):
                log_entry[key] = val
        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


# Keys that exist on every LogRecord (don't include in "extra" fields)
_STDLIB_LOG_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


class _TextFormatter(logging.Formatter):
    """Human-readable formatter with optional colour on terminals."""

    _COLOURS = {
        "DEBUG": "\033[36m",  # cyan
        "INFO": "\033[32m",  # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[35m",  # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        use_colour = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        level = record.levelname
        if use_colour:
            colour = self._COLOURS.get(level, "")
            level_str = f"{colour}{level:<8}{self._RESET}"
        else:
            level_str = f"{level:<8}"
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        msg = record.getMessage()
        base = f"{ts} {level_str} [{record.name}] {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(
    level: str | None = None,
    fmt: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure root tokenpak logger.

    Call once at startup. Subsequent calls are no-ops unless *force=True*
    (not implemented — restart the process to reconfigure).

    Args:
        level: Override ``TPK_LOG_LEVEL`` env var. One of DEBUG/INFO/WARNING/ERROR/CRITICAL.
        fmt: Override ``TPK_LOG_FORMAT`` env var. "json" or "text".
        log_file: Override ``TPK_LOG_FILE`` env var. Path to write logs to.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_str = (level or os.environ.get("TPK_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_str, logging.INFO)

    fmt_str = (fmt or os.environ.get("TPK_LOG_FORMAT", "text")).lower()
    formatter: logging.Formatter = _JsonFormatter() if fmt_str == "json" else _TextFormatter()

    logger = logging.getLogger(TPK_LOGGER_NAME)
    logger.setLevel(log_level)
    logger.propagate = False  # don't double-log to root

    # Stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(log_level)
    logger.addHandler(stderr_handler)

    # Optional file handler
    file_path = log_file or os.environ.get("TPK_LOG_FILE")
    if file_path:
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the tokenpak namespace.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` named ``tokenpak.<name>`` (or just
        ``tokenpak`` if *name* already starts with that prefix).

    Example::

        logger = get_logger(__name__)
        logger.info("Proxy request", extra={"path": "/v1/messages", "tokens": 512})
    """
    if name.startswith(TPK_LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{TPK_LOGGER_NAME}.{name}")
