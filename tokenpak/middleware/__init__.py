"""Backwards-compat shim — see proxy.middleware.

Canonical home is ``tokenpak.proxy.middleware`` (Architecture §1 —
proxy owns HTTP middleware). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

# Re-export everything the new location exposes so legacy
# `from tokenpak.middleware import X` imports keep working.
from tokenpak.proxy.middleware import *  # noqa: F401,F403
from tokenpak.proxy.middleware import __all__  # noqa: F401

warnings.warn(
    "tokenpak.middleware is deprecated — "
    "import from tokenpak.proxy.middleware (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
