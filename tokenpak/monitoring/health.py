"""Backwards-compat shim — see proxy.health.

Canonical home is ``tokenpak.proxy.health`` (Architecture §1 —
proxy owns its own liveness/health concerns; the single caller is
proxy.api.routes). Moved 2026-04-20 per D1 migration. Removal target:
TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.proxy.health import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.monitoring.health is deprecated — "
    "import from tokenpak.proxy.health. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
