"""failure_memory.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.failure_memory import (
        FailureMemoryDB,
        FailureSignature,
        N_VALIDATE_SUCCESSES,
    )

    __all__ = [
        "FailureMemoryDB",
        "FailureSignature",
        "N_VALIDATE_SUCCESSES",
    ]
except ImportError:
    raise ImportError(
        "failure_memory requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
