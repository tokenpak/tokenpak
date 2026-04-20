"""Backwards-compat shim — see sources.walker.

Canonical home is ``tokenpak.sources.walker`` (Architecture §1 —
sources owns this concern). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.sources.walker import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.walker is deprecated — "
    "import from tokenpak.sources.walker. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
