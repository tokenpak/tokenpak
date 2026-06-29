"""TokenPak Dispatch record models.

This package authors the twelve v0.1-alpha Dispatch records as Pydantic v2
models. Ten are transcribed verbatim from the canonical record schemas;
``DispatchArtifact`` and ``DispatchPolicy`` are faithful sketches (the canonical
schema lists them without a full field schema — see their module
docstrings).

:data:`DISPATCH_RECORD_MODELS` is the canonical name→model registry. On import
the registry is checked against the Pak-suffix collision rule: if any record
class name ends with ``Pak`` the import fails loud (Dispatch records are
internal execution records, NOT Paks).
"""

from __future__ import annotations

# Pydantic is the contract layer for every Dispatch record model. Guard the
# import at this package boundary so a slim install — one that
# lacks the opt-in ``dispatch`` extra — fails with an actionable install hint
# rather than a raw ImportError from deep inside the submodule import chain.
try:
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover - exercised only on slim installs
    raise ImportError(
        "TokenPak Dispatch record models require pydantic. Install the dispatch "
        "extra: `pip install tokenpak[dispatch]`."
    ) from exc

from .artifact import DispatchArtifact
from .common import (
    AcceptanceCriterion,
    Constraint,
    Deliverable,
    DispatchBaseModel,
    ManifestPermissions,
    PathPolicy,
    QualityRequirements,
    StationLoopPolicy,
    WorkerLoopDefault,
)
from .decision import (
    DecisionDefaultAction,
    DecisionOption,
    DecisionRecommendation,
    DecisionResolution,
    DispatchDecision,
)
from .effect import DispatchEffect
from .job import DispatchJob
from .late_result import LateResult
from .manifest import DispatchManifest
from .policy import DispatchPolicy
from .receipt import (
    DispatchReceipt,
    ReceiptDecision,
    ReceiptEffect,
    ReceiptStation,
    ReceiptTelemetry,
)
from .route import (
    DispatchRoute,
    RouteDelivery,
    RouteRetryPolicy,
    RouteStation,
    RouteTriggers,
)
from .run import DispatchRun
from .station_run import DispatchStationRun
from .worker import DispatchWorker, WorkerPermissionProfile


class PakSuffixCollisionError(RuntimeError):
    """Raised at import if a Dispatch record name ends with ``Pak``."""


# Canonical registry of the twelve v0.1-alpha Dispatch records (the record
# vocabulary). Order follows the canonical record list.
DISPATCH_RECORD_MODELS: dict[str, type[BaseModel]] = {
    "DispatchJob": DispatchJob,
    "DispatchManifest": DispatchManifest,
    "DispatchRoute": DispatchRoute,
    "DispatchRun": DispatchRun,
    "DispatchStationRun": DispatchStationRun,
    "DispatchDecision": DispatchDecision,
    "DispatchReceipt": DispatchReceipt,
    "DispatchEffect": DispatchEffect,
    "LateResult": LateResult,
    "DispatchArtifact": DispatchArtifact,
    "DispatchWorker": DispatchWorker,
    "DispatchPolicy": DispatchPolicy,
}


def _assert_no_pak_suffix(models: dict[str, type[BaseModel]]) -> None:
    """Fail-loud Pak-suffix collision check: no record name may end with ``Pak``."""

    offenders = sorted(name for name in models if name.endswith("Pak"))
    if offenders:
        raise PakSuffixCollisionError(
            "Dispatch records must not use the *Pak suffix (Dispatch "
            f"records are execution records, not Paks). Offending names: {offenders}."
        )


def load_dispatch_models() -> dict[str, type[BaseModel]]:
    """Return the validated Dispatch record registry (fail-loud on *Pak names)."""

    _assert_no_pak_suffix(DISPATCH_RECORD_MODELS)
    return dict(DISPATCH_RECORD_MODELS)


# Enforce the collision rule at import time.
_assert_no_pak_suffix(DISPATCH_RECORD_MODELS)


__all__ = [
    # Registry + loader
    "DISPATCH_RECORD_MODELS",
    "PakSuffixCollisionError",
    "load_dispatch_models",
    # Base + shared
    "DispatchBaseModel",
    "AcceptanceCriterion",
    "Constraint",
    "Deliverable",
    "PathPolicy",
    "ManifestPermissions",
    "QualityRequirements",
    "StationLoopPolicy",
    "WorkerLoopDefault",
    # The twelve records
    "DispatchJob",
    "DispatchManifest",
    "DispatchRoute",
    "DispatchRun",
    "DispatchStationRun",
    "DispatchDecision",
    "DispatchReceipt",
    "DispatchEffect",
    "LateResult",
    "DispatchArtifact",
    "DispatchWorker",
    "DispatchPolicy",
    # Nested route/decision/receipt/worker structures
    "RouteStation",
    "RouteTriggers",
    "RouteRetryPolicy",
    "RouteDelivery",
    "DecisionOption",
    "DecisionRecommendation",
    "DecisionDefaultAction",
    "DecisionResolution",
    "ReceiptStation",
    "ReceiptDecision",
    "ReceiptEffect",
    "ReceiptTelemetry",
    "WorkerPermissionProfile",
]
