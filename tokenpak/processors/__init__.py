"""Backwards-compat shim — see compression.processors.

Canonical home is ``tokenpak.compression.processors`` (Architecture §1 —
compression owns per-content-type processors). Moved 2026-04-20 per
D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.processors import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.processors is deprecated — "
    "import from tokenpak.compression.processors (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
