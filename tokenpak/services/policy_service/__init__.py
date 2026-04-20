"""Policy gates: budget, cost, rate-limit, content-policy (Phase 2 wrapper).

Serves the ``security`` stage name in the canonical pipeline. Policy
decisions are consulted against primitive modules: ``tokenpak.security/``
for DLP + permissions (currently migrating from ``tokenpak/creds/`` per
D1), ``tokenpak.budget`` or the budget_controller for spend gates.

Phase 2 pass-through. The stage exists so the pipeline emits a
canonical "security" slot even before policy logic migrates; this
preserves the Architecture §1.3 invariant that the stage sequence is
stable.
"""

from __future__ import annotations

from ..request_pipeline.stages import PipelineContext


class Stage:
    """Security / policy pipeline stage."""

    name = "security"

    def apply_request(self, ctx: PipelineContext) -> None:
        """Apply DLP redaction + budget + rate-limit gates.

        Phase 2 pass-through. Full implementation: run DLP redactor
        over ``ctx.request.body``, check budget against cached spend
        state, apply per-provider rate-limit gates, raise a canonical
        TIP error (core/contracts/errors.py) if any gate denies.
        """
        return None

    def apply_response(self, ctx: PipelineContext) -> None:
        """Phase 2 pass-through."""
        return None
