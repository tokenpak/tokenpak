"""Backwards-compat shim — see compression.pack.

Canonical home is ``tokenpak.compression.pack`` (Architecture §1 —
compression owns the ContextPack API and compile-report machinery).
Moved 2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.pack import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.pack is deprecated — "
    "import from tokenpak.compression.pack. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
