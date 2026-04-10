"""TokenPak telemetry sub-package.

Exposes canonical types, provider adapters, and the adapter registry.
The 4A agent owns models.py, storage.py, and pricing.py.
This module (4B) owns canonical.py and the adapters/ sub-package.
"""

from __future__ import annotations

try:
    from tokenpak.telemetry.cost_tracker import CostTracker as CompletionTracker
except ImportError:
    CompletionTracker = None  # type: ignore[assignment,misc]
from tokenpak.telemetry.adapters.registry import AdapterRegistry
from tokenpak.telemetry.cache import CacheStore as CacheManager
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)

# Public API additions — TelemetryCollector and CompletionTracker
from tokenpak.telemetry.collector import TelemetryCollector

__all__ = [
    "CanonicalRequest",
    "CanonicalResponse",
    "CanonicalUsage",
    "UsageSource",
    "Confidence",
    "AdapterRegistry",
    # Public API
    "TelemetryCollector",
    "CompletionTracker",
    "CacheManager",
]
