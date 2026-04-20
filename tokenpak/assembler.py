"""Backwards-compat shim — see compression.assembler.

Canonical home is ``tokenpak.compression.assembler`` (Architecture §1 —
compression owns this concern). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.assembler import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.assembler is deprecated — "
    "import from tokenpak.compression.assembler. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
