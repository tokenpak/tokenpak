"""retrieval_watchdog.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.retrieval_watchdog import (
        HealthStatus,
        RetrievalMetrics,
        HealthAlert,
        RetrievalWatchdog,
    )

    __all__ = [
        "HealthStatus",
        "RetrievalMetrics",
        "HealthAlert",
        "RetrievalWatchdog",
    ]
except ImportError:
    raise ImportError(
        "retrieval_watchdog requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
