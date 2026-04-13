"""tokenpak/agent/query — Phase 5B: JSONL-based Query API."""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.query is deprecated, use tokenpak._internal.query instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ['api', 'audit', 'timeline']
