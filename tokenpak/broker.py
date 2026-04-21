"""Backwards-compat shim — see routing.broker.

Canonical home is ``tokenpak.routing.broker`` (Architecture §1 —
routing owns this concern). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.routing.broker import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.broker is deprecated — "
    "import from tokenpak.routing.broker. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
