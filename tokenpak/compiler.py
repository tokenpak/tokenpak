"""Backwards-compat shim — see compression.compiler.

Canonical home is ``tokenpak.compression.compiler`` (Architecture §1 —
compression owns the compile-time orchestration that turns blocks
into wire-format payload). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.compiler import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.compiler is deprecated — "
    "import from tokenpak.compression.compiler. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
