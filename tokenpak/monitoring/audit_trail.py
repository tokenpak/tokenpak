"""Backwards-compat shim — see telemetry.audit_trail.

Canonical home is ``tokenpak.telemetry.audit_trail`` (Architecture §1 —
telemetry owns audit-friendly request metadata + logging; audit_trail
builds on telemetry.request_logger). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.telemetry.audit_trail import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.monitoring.audit_trail is deprecated — "
    "import from tokenpak.telemetry.audit_trail. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
