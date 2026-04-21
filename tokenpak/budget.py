"""Backwards-compat shim — see services.policy_service.budget.rules."""
from __future__ import annotations
import warnings
from tokenpak.services.policy_service.budget.rules import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.budget is deprecated — "
    "import from tokenpak.services.policy_service.budget.rules. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
