"""stability_scorer.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.regression.stability_scorer import (
        StabilityRating,
        OutputMetrics,
    )

    __all__ = [
        "StabilityRating",
        "OutputMetrics",
    ]
except ImportError:
    raise ImportError(
        "stability_scorer requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
