"""request_logger.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.features.monitoring.request_logger import (
        RequestLogger,
        RequestLogRecord,
        log_request,
        new_request_id,
        LEVEL_DEBUG,
        LEVEL_INFO,
        LEVEL_WARN,
    )

    __all__ = [
        "RequestLogger",
        "RequestLogRecord",
        "log_request",
        "new_request_id",
        "LEVEL_DEBUG",
        "LEVEL_INFO",
        "LEVEL_WARN",
    ]
except ImportError:
    raise ImportError(
        "request_logger requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
