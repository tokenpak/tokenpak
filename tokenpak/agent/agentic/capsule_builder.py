"""capsule_builder.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.capsule_builder import (
        CapsuleMetadata,
        CapsuleConfig,
        Capsule,
        CapsuleBuilder,
    )

    __all__ = [
        "CapsuleMetadata",
        "CapsuleConfig",
        "Capsule",
        "CapsuleBuilder",
    ]
except ImportError:
    raise ImportError(
        "capsule_builder requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
