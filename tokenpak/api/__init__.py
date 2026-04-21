"""Backwards-compat shim — see proxy.api.

Canonical home is ``tokenpak.proxy.api`` (Architecture §1 — proxy
owns HTTP server + API surfaces). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.proxy.api import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.api is deprecated — "
    "import from tokenpak.proxy.api (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
