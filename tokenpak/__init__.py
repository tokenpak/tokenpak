"""TokenPak — Efficient API token caching and compression proxy."""

__version__ = "0.5.0"
__author__ = "Kevin Yang"
__license__ = "MIT"

# Handoff protocol exports
try:
    from tokenpak.agent.agentic.handoff import (
        TokenPak,
        Handoff,
        HandoffBlock,
        HandoffManager,
        ContextRef,
        HandoffStatus,
        HandoffWire,
    )
    __all__ = [
        "__version__",
        "TokenPak",
        "Handoff",
        "HandoffBlock",
        "HandoffManager",
        "ContextRef",
        "HandoffStatus",
        "HandoffWire",
    ]
except ImportError:
    __all__ = ["__version__"]
