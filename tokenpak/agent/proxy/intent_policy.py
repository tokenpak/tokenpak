"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.intent_policy``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.intent_policy is a deprecated re-export; "
    "import from tokenpak.proxy.intent_policy instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.intent_policy import *  # noqa: F401,F403,E402

__all__ = ["Any", "CANONICAL_INTENTS", "CONFIDENCE_THRESHOLD", "DecisionAction", "Dict", "FALLBACK_POLICY", "Optional", "PolicyResult", "RoutingDecision", "Tuple", "apply_context_contract", "dataclass", "decide", "field", "is_known_intent", "known_intents", "resolve_policy"]
