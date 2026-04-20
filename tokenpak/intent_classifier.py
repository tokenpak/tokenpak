"""Backwards-compat shim — see routing.intent_classifier.

Canonical home is ``tokenpak.routing.intent_classifier`` (Architecture §1 —
routing owns this concern). Moved 2026-04-20 per D1 migration.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.routing.intent_classifier import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.intent_classifier is deprecated — "
    "import from tokenpak.routing.intent_classifier. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
