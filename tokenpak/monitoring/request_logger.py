"""Backwards-compat shim — see telemetry.request_logger.

Canonical home is ``tokenpak.telemetry.request_logger`` (Architecture
§1 — telemetry owns wire-side request logging). Moved 2026-04-20
per D1 migration. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings

from tokenpak.telemetry.request_logger import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.monitoring.request_logger is deprecated — "
    "import from tokenpak.telemetry.request_logger. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
