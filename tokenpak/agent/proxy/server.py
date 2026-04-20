"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.server``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "tokenpak.agent.proxy.server is a deprecated re-export; "
    "import from tokenpak.proxy.server instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.server import *  # noqa: F401,F403,E402
