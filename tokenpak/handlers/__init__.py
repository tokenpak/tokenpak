"""Backwards-compat shim — see proxy.handlers.

Canonical home is ``tokenpak.proxy.handlers`` (Architecture §1 —
proxy owns request-lifecycle handlers). Moved 2026-04-20 per D1
migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "tokenpak.handlers is deprecated — "
    "import from tokenpak.proxy.handlers (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
