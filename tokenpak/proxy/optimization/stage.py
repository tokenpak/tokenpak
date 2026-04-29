"""Stage interface for the optimization pipeline.

Every stage implements ``eligible(ctx)`` (cheap, side-effect-free) and
``apply(ctx)`` (the actual work). In observe-only mode the pipeline calls
ONLY ``eligible``. Stages SHOULD NOT mutate ``ctx`` in ``eligible``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .context import OptimizationContext


@dataclass(frozen=True)
class EligibilityResult:
    """Stage eligibility verdict.

    eligible:   True if the stage would run under the current contract.
    skip_reason: machine-readable token explaining why a stage skipped
                 (empty when eligible=True). Examples: ``flag-off``,
                 ``capability-missing``, ``route-class-blocked``,
                 ``fidelity-too-strict``, ``no-op-default``.
    detail:     optional human-readable note for debugging / dashboards.
    """

    eligible: bool
    skip_reason: str = ""
    detail: str = ""


@runtime_checkable
class OptimizationStage(Protocol):
    """Protocol every optimization stage must satisfy.

    Subclasses set ``name`` (machine-readable identifier emitted in traces)
    and ``required_capabilities`` (set of TIP capability label strings the
    contract must report present for this stage to be eligible).
    """

    name: str
    required_capabilities: frozenset

    def eligible(self, ctx: "OptimizationContext") -> EligibilityResult: ...

    def apply(self, ctx: "OptimizationContext") -> "OptimizationContext": ...


@dataclass
class NoOpStage:
    """Reference stage that is always eligible-but-skips with ``no-op-default``.

    Used as a sentinel in the registry tests and as a safe placeholder for
    stages whose mutating implementation lives in a later milestone.
    """

    name: str = "no-op"
    required_capabilities: frozenset = field(default_factory=frozenset)

    def eligible(self, ctx: "OptimizationContext") -> EligibilityResult:
        return EligibilityResult(eligible=False, skip_reason="no-op-default")

    def apply(self, ctx: "OptimizationContext") -> "OptimizationContext":
        return ctx
