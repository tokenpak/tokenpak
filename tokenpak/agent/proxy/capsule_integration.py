"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.capsule_integration``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "tokenpak.agent.proxy.capsule_integration is a deprecated re-export; "
    "import from tokenpak.proxy.capsule_integration instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.capsule_integration import *  # noqa: F401,F403,E402
