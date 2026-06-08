"""TokenPak Dispatch stations — the per-station execution contracts.

A *station* is one step in a :class:`~tokenpak.orchestration.dispatch.models.route.DispatchRoute`.
This package hosts the station-level I/O contracts and runners that the
sequential FulfillmentLine runner (a later packet) drives.

v0.1-alpha ships the **Reviewer Station** (:mod:`.reviewer`): the single
semantic-review station that validates *substance* (does the build satisfy the
acceptance criteria / constraints?) as opposed to the deterministic Gatehouse,
which validates *structure*. The Reviewer Station performs exactly one LLM call
per review, routed through TIP; the dispatch runtime is not built yet, so the
station takes an injected client (see :class:`.reviewer.ReviewerLLM`) rather
than wiring a provider directly.
"""

from __future__ import annotations

from .reviewer import (
    CriterionResult,
    DeliveryRecommendation,
    RequiredFix,
    ReviewerLLM,
    ReviewerOutputError,
    ReviewerRiskFlag,
    ReviewerStation,
    ReviewerStationInput,
    ReviewerStationResult,
    ReviewerStatus,
)

__all__ = [
    "ReviewerStation",
    "ReviewerStationInput",
    "ReviewerStationResult",
    "ReviewerStatus",
    "CriterionResult",
    "RequiredFix",
    "ReviewerRiskFlag",
    "DeliveryRecommendation",
    "ReviewerLLM",
    "ReviewerOutputError",
]
