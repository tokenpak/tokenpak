"""Backwards-compat shim — see compression.vendor_classifier.

Canonical home is ``tokenpak.compression.vendor_classifier`` (Architecture §1 —
compression owns this concern). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.vendor_classifier import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.vendor_classifier is deprecated — "
    "import from tokenpak.compression.vendor_classifier. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
