"""Backwards-compat shim — see core.validation.

Canonical home is ``tokenpak.core.validation`` (Architecture §1 —
core owns "schema/contract validation, shared data structures").
Moved 2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.core.validation import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.validation is deprecated — "
    "import from tokenpak.core.validation (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
