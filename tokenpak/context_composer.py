"""Backwards-compat shim — see compression.context_composer.

Canonical home is ``tokenpak.compression.context_composer``
(Architecture §1 — compression owns context composition / packing).
Moved 2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.context_composer import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.context_composer is deprecated — "
    "import from tokenpak.compression.context_composer. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
