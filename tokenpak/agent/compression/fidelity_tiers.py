"""fidelity_tiers.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.compression.fidelity_tiers import (
        FidelityTier,
        TierConfig,
        TierManager,
    )

    __all__ = [
        "FidelityTier",
        "TierConfig",
        "TierManager",
    ]
except ImportError:
    raise ImportError(
        "fidelity_tiers requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
