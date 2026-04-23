"""Backwards-compat shim — see tokenpak.core.state.manager."""
from __future__ import annotations

import warnings

from tokenpak.core.state.manager import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.state_manager is deprecated — import from tokenpak.core.state.manager. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
