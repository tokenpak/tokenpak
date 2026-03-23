"""intent_policy.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.proxy.intent_policy import (
        Intent,
        IntentDetectionResult,
    )

    __all__ = [
        "Intent",
        "IntentDetectionResult",
    ]
except ImportError:
    raise ImportError(
        "intent_policy requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
