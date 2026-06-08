"""TokenPak Dispatch — OSS workflow-control layer (Standards Delta v0).

This package hosts the Dispatch record schemas (Pydantic v2 models + JSON
Schema exports), the capability registry, and the dispatch runtime that
underpin TokenPak Dispatch's v0.1-alpha. The schema foundation was authored by
P-SCHEMA-01; the FrontDock intake (P-FRONTDOCK-01), Run Ledger (P-LEDGER-01),
ContextProvider (P-CONTEXT-01), worker registry (P-WORKERS-01), and
Gatehouse/Reviewer (P-GATEHOUSE-REVIEWER-01) followed. The deterministic
route-selection runtime (:class:`~tokenpak.orchestration.dispatch.dispatch.DispatchRuntime`,
P-RUNTIME-01) wires FrontDock output → a selected route; station *execution*
lands in P-EXEC-01.

Technical namespace (Standards Delta v0 §2): module ``tokenpak/orchestration/
dispatch/``, CLI verb ``tokenpak dispatch``, MCP prefix ``dispatch.*``,
on-disk root ``~/.tpk/dispatch/`` (pending a path-config amendment). Dispatch
records are internal execution records, NOT Paks.
"""

from __future__ import annotations

from tokenpak.orchestration.dispatch.dispatch import (
    DISPATCH_SCORING_METADATA,
    DispatchRuntime,
    ProjectRules,
    RouteScore,
    RouteSuggester,
    RouteSuggestion,
    SelectionOutcome,
    score_route,
)
from tokenpak.orchestration.dispatch.models import (
    DISPATCH_RECORD_MODELS,
    PakSuffixCollisionError,
    load_dispatch_models,
)
from tokenpak.orchestration.dispatch.registry.capabilities import (
    DISPATCH_CAPABILITIES,
    UnknownCapabilityError,
    validate_capabilities,
)
from tokenpak.orchestration.dispatch.registry.routes import (
    DispatchRouteRegistry,
    RouteResolutionError,
    bind_route,
    resolve_station_workers,
)

__all__ = [
    "DISPATCH_RECORD_MODELS",
    "PakSuffixCollisionError",
    "load_dispatch_models",
    "DISPATCH_CAPABILITIES",
    "UnknownCapabilityError",
    "validate_capabilities",
    # Route registry + dynamic binding
    "DispatchRouteRegistry",
    "RouteResolutionError",
    "bind_route",
    "resolve_station_workers",
    # Dispatch runtime + deterministic route selection
    "DispatchRuntime",
    "SelectionOutcome",
    "ProjectRules",
    "RouteScore",
    "score_route",
    "RouteSuggester",
    "RouteSuggestion",
    "DISPATCH_SCORING_METADATA",
]
