"""request_logger.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.features.monitoring.request_logger import (
        RequestLogRecord,
    )

    __all__ = [
        "RequestLogRecord",
    ]
except ImportError:
    raise ImportError(
        "request_logger requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
