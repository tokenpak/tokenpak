"""TokenPak Event Trigger Framework — deterministic, zero-LLM event-driven actions."""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.triggers is deprecated, use tokenpak._internal.triggers instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .daemon import TriggerDaemon
from .matcher import match_event
from .store import Trigger, TriggerStore

__all__ = ["TriggerStore", "Trigger", "match_event", "TriggerDaemon"]
