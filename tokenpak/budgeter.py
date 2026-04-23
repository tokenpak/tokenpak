"""Backwards-compat shim — see services.policy_service.budget.budgeter."""
from __future__ import annotations

import warnings

from tokenpak.services.policy_service.budget.budgeter import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.budgeter is deprecated — "
    "import from tokenpak.services.policy_service.budget.budgeter. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
