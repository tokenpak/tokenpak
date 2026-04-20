"""Backwards-compat shim — see debug.trace.

Canonical home is ``tokenpak.debug.trace`` (Architecture §1 — debug
owns structured traces + diagnostic logs). Moved 2026-04-20 per D1
migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.debug.trace import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.trace is deprecated — "
    "import from tokenpak.debug.trace. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
