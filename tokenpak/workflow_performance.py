"""Backwards-compat shim — see tokenpak.telemetry.workflow_performance."""
from __future__ import annotations

import warnings

from tokenpak.telemetry.workflow_performance import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.workflow_performance is deprecated — import from tokenpak.telemetry.workflow_performance. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
