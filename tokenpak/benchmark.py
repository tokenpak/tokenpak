"""Backwards-compat shim — see debug.benchmark.

Canonical home is ``tokenpak.debug.benchmark`` (Architecture §1 —
debug owns diagnostics). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.debug.benchmark import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.benchmark is deprecated — "
    "import from tokenpak.debug.benchmark. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
