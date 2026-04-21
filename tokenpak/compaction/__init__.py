"""Backwards-compat shim — see compression.compaction.

Canonical home is ``tokenpak.compression.compaction`` (Architecture §1 —
compaction is a compression concern: reduces context size via mode-
based policies). Moved 2026-04-20 per D1 migration. Removal target:
TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.compaction import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.compaction is deprecated — "
    "import from tokenpak.compression.compaction (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
