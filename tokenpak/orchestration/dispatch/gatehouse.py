"""Deterministic Gatehouse — structural validation only.

The Gatehouse validates **structure**, never **substance**. It runs a fixed set
of deterministic checks (NO LLM call, NO network, no provider) over a dispatch
manifest, route, station outputs, permissions, and the assembled delivery
package, and it decides the Delivery Gate purely from those checks plus the
Reviewer Station's verdict.

**The Gatehouse does NOT and must NOT claim semantic correctness.** Whether the
work actually satisfies the acceptance criteria is the Reviewer Station's job
(:mod:`.stations.reviewer`). The Gatehouse only confirms that the *shapes* are
present and valid (manifest is complete, the route/stations parse, acceptance
criteria exist, station outputs are schema-valid, permission constraints hold,
the delivery package carries its required pieces). A green Gatehouse means
"structurally shippable", not "correct".

Reviewer → Gatehouse handoff (handoff table):

* Reviewer ``pass`` → Delivery Gate proceeds; package shipped.
* Reviewer ``warning`` → Gatehouse creates a :class:`DispatchDecision`
  (accept/reject). User accept → ``delivery_ready_with_warning``; user reject →
  ``blocked``.
* Reviewer ``fail`` → Delivery Gate blocks. v0.1-alpha returns a BLOCKED
  :class:`DeliveryPackage` carrying ``required_fixes``. **No automatic repair
  loop.**

Every delivery package built from a route that used a Reviewer Station carries
the cost note: "This route uses a Reviewer Station; +1 LLM call vs single-shot
execution."
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import Field, ValidationError

from .models.common import DispatchBaseModel
from .models.decision import (
    DecisionDefaultAction,
    DecisionOption,
    DecisionRecommendation,
    DispatchDecision,
)
from .models.enums import (
    AutoApplyAfter,
    DecisionScope,
    DecisionStatus,
    RiskLevel,
)
from .models.manifest import DispatchManifest
from .models.route import DispatchRoute, RouteStation
from .models.station_run import DispatchStationRun
from .stations.reviewer import (
    RequiredFix,
    ReviewerStationResult,
    ReviewerStatus,
)

# Cost note: emitted on any delivery package whose
# route used a Reviewer Station. Single source of truth — both the package
# builder and tests read this constant.
REVIEWER_COST_NOTE = (
    "This route uses a Reviewer Station; +1 LLM call vs single-shot execution."
)

# The handoff outcomes, expressed as the delivery-package status values the
# Gatehouse can emit. Kept distinct from the reviewer's own
# ``delivery_recommendation`` enum because the Gatehouse adds the decision-gated
# outcomes (warning still pending a decision, and the user-accept/reject splits).
class DeliveryStatus(str, Enum):
    """Delivery-package status emitted by the Gatehouse Delivery Gate.

    * ``delivery_ready`` — Reviewer ``pass``; Delivery Gate proceeds.
    * ``decision_required`` — Reviewer ``warning`` and no user resolution yet; a
      :class:`DispatchDecision` has been created.
    * ``delivery_ready_with_warning`` — Reviewer ``warning`` + user accepted.
    * ``blocked`` — Reviewer ``fail`` (carries ``required_fixes``), or Reviewer
      ``warning`` + user rejected, or a failed deterministic check.
    """

    DELIVERY_READY = "delivery_ready"
    DECISION_REQUIRED = "decision_required"
    DELIVERY_READY_WITH_WARNING = "delivery_ready_with_warning"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Deterministic check results
# ---------------------------------------------------------------------------


class GatehouseCheckResult(DispatchBaseModel):
    """Outcome of one deterministic Gatehouse check (structural only)."""

    name: str
    passed: bool
    detail: str = ""


class GatehouseReport(DispatchBaseModel):
    """Aggregate of every deterministic Gatehouse check for one delivery."""

    checks: list[GatehouseCheckResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True iff every deterministic check passed (structurally shippable)."""

        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[GatehouseCheckResult]:
        """The failed deterministic checks (empty when structurally clean)."""

        return [c for c in self.checks if not c.passed]


class DeliveryPackage(DispatchBaseModel):
    """The Gatehouse's Delivery Gate verdict + assembled package.

    Carries the deterministic :class:`GatehouseReport`, the delivery status, the
    reviewer-derived ``required_fixes`` (populated when blocked on a reviewer
    ``fail``), the optional :class:`DispatchDecision` (created on a reviewer
    ``warning``), and — whenever the route used a Reviewer Station — the
    cost note. The Gatehouse never asserts semantic correctness here; ``status``
    reflects structure + the reviewer's own verdict, nothing more.
    """

    job_id: str
    status: DeliveryStatus
    gatehouse_report: GatehouseReport
    required_fixes: list[RequiredFix] = Field(default_factory=list)
    decision: DispatchDecision | None = None
    cost_note: str | None = None
    reviewer_status: ReviewerStatus | None = None
    summary: str = ""


# ---------------------------------------------------------------------------
# The deterministic checks
# ---------------------------------------------------------------------------


class Gatehouse:
    """Deterministic structural validator + Delivery Gate.

    No LLM, no network, no semantic correctness claims. Each ``check_*`` method
    is a pure deterministic predicate returning a :class:`GatehouseCheckResult`.
    :meth:`run_checks` runs the full battery; :meth:`evaluate_delivery` combines
    the structural report with a :class:`ReviewerStationResult` to produce a
    :class:`DeliveryPackage` per the handoff table.
    """

    # ---- individual deterministic checks ----------------------------------

    def check_manifest_completeness(
        self, manifest: DispatchManifest
    ) -> GatehouseCheckResult:
        """manifest_completeness — required manifest fields are present & non-empty.

        Structural only: confirms ``goal``, ``route_id``, ``job_id`` are set and
        a permissions block exists. Says nothing about whether the goal is the
        *right* goal (that is semantic — Reviewer's job).
        """

        missing: list[str] = []
        if not manifest.job_id:
            missing.append("job_id")
        if not manifest.route_id:
            missing.append("route_id")
        if not manifest.goal:
            missing.append("goal")
        if manifest.permissions is None:  # pragma: no cover - typed required
            missing.append("permissions")
        if missing:
            return GatehouseCheckResult(
                name="manifest_completeness",
                passed=False,
                detail=f"manifest missing required field(s): {sorted(missing)}",
            )
        return GatehouseCheckResult(
            name="manifest_completeness", passed=True, detail="manifest fields present"
        )

    def check_route_station_schema(self, route: Any) -> GatehouseCheckResult:
        """route/station schema validity — the route parses as a DispatchRoute.

        Accepts either a constructed :class:`DispatchRoute` or a raw mapping;
        the mapping is validated against the model (fail-loud → check fail). Each
        station must declare its ``output_schema`` and resolve to exactly one of
        worker (``required_role``) or system (``system_component``).
        """

        try:
            model = route if isinstance(route, DispatchRoute) else DispatchRoute.model_validate(route)
        except ValidationError as exc:
            return GatehouseCheckResult(
                name="route_station_schema",
                passed=False,
                detail=f"route failed schema validation: {exc.error_count()} error(s)",
            )
        problems = [
            problem
            for station in model.stations
            if (problem := self._station_shape_problem(station)) is not None
        ]
        if problems:
            return GatehouseCheckResult(
                name="route_station_schema",
                passed=False,
                detail="; ".join(problems),
            )
        return GatehouseCheckResult(
            name="route_station_schema",
            passed=True,
            detail=f"route + {len(model.stations)} station(s) structurally valid",
        )

    @staticmethod
    def _station_shape_problem(station: RouteStation) -> str | None:
        if not station.output_schema:
            return f"station {station.id!r} missing output_schema"
        has_role = bool(station.required_role)
        has_component = bool(station.system_component)
        if has_role == has_component:
            return (
                f"station {station.id!r} must set exactly one of required_role / "
                "system_component"
            )
        return None

    def check_acceptance_criteria_presence(
        self, manifest: DispatchManifest
    ) -> GatehouseCheckResult:
        """acceptance-criteria presence — at least one acceptance criterion exists.

        Structural: there is *something* to review against. Does not judge
        whether the criteria are met (Reviewer) or well-written (semantic).
        """

        count = len(manifest.acceptance_criteria)
        if count == 0:
            return GatehouseCheckResult(
                name="acceptance_criteria_presence",
                passed=False,
                detail="manifest declares no acceptance criteria",
            )
        return GatehouseCheckResult(
            name="acceptance_criteria_presence",
            passed=True,
            detail=f"{count} acceptance criterion/criteria present",
        )

    def check_station_output_schema(
        self,
        station_runs: list[DispatchStationRun],
        *,
        validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    ) -> GatehouseCheckResult:
        """station output schema validity — completed stations carry valid output.

        For each station run with status ``completed``, ``result_payload`` must
        be present and (when a validator for its ``result_schema_version`` is
        supplied via ``validators``) must validate against that schema. Failed
        stations are not required to carry output. This is "always dynamic": the
        schema set is injected, never hardcoded here.
        """

        validators = validators or {}
        problems: list[str] = []
        completed = 0
        for run in station_runs:
            if run.status.value != "completed":
                continue
            completed += 1
            if run.result_payload is None:
                problems.append(f"station run {run.id!r} completed with no result_payload")
                continue
            validator = validators.get(run.result_schema_version)
            if validator is None:
                continue
            try:
                validator(run.result_payload)
            except Exception as exc:  # noqa: BLE001 - any validator failure is a fail
                problems.append(
                    f"station run {run.id!r} output failed "
                    f"{run.result_schema_version!r} validation: {exc}"
                )
        if problems:
            return GatehouseCheckResult(
                name="station_output_schema",
                passed=False,
                detail="; ".join(problems),
            )
        return GatehouseCheckResult(
            name="station_output_schema",
            passed=True,
            detail=f"{completed} completed station output(s) structurally valid",
        )

    def check_permission_constraints(
        self, manifest: DispatchManifest
    ) -> GatehouseCheckResult:
        """permission constraints — manifest permissions are internally consistent.

        Structural consistency only: an action may not appear in both
        ``allowed_actions`` and ``forbidden_actions`` (a contradictory grant),
        and the mandatory denied path globs must be present on the path policy
        (the path-policy model injects them, so this asserts the safety floor
        held rather than re-deciding policy).
        """

        from .models.common import MANDATORY_DENIED_PATHS

        perms = manifest.permissions
        allowed = set(perms.allowed_actions)
        forbidden = set(perms.forbidden_actions)
        contradictions = sorted(allowed & forbidden)
        missing_denied = [
            glob
            for glob in MANDATORY_DENIED_PATHS
            if glob not in manifest.path_policy.denied_paths
        ]
        problems: list[str] = []
        if contradictions:
            problems.append(
                f"action(s) both allowed and forbidden: {contradictions}"
            )
        if missing_denied:
            problems.append(
                f"path policy missing mandatory denied glob(s): {missing_denied}"
            )
        if problems:
            return GatehouseCheckResult(
                name="permission_constraints",
                passed=False,
                detail="; ".join(problems),
            )
        return GatehouseCheckResult(
            name="permission_constraints",
            passed=True,
            detail="permissions internally consistent; mandatory denied paths present",
        )

    def check_delivery_package_completeness(
        self,
        route: DispatchRoute,
        package_fields: dict[str, Any],
    ) -> GatehouseCheckResult:
        """delivery package completeness — every route-required piece is present.

        The route's :class:`RouteDelivery` flags declare which pieces the package
        must include (summary / files_changed / tests / risks / next_steps).
        This check is "always dynamic": it iterates the delivery flags rather
        than hardcoding the piece names, so adding a delivery flag automatically
        extends the check.
        """

        delivery = route.delivery
        missing: list[str] = []
        for flag_name, required in delivery.model_dump().items():
            if not required:
                continue
            piece = flag_name.removeprefix("include_")
            value = package_fields.get(piece)
            if value is None or (hasattr(value, "__len__") and len(value) == 0):
                missing.append(piece)
        if missing:
            return GatehouseCheckResult(
                name="delivery_package_completeness",
                passed=False,
                detail=f"delivery package missing required piece(s): {sorted(missing)}",
            )
        return GatehouseCheckResult(
            name="delivery_package_completeness",
            passed=True,
            detail="delivery package carries every route-required piece",
        )

    # ---- battery + delivery gate ------------------------------------------

    def run_checks(
        self,
        *,
        manifest: DispatchManifest,
        route: DispatchRoute,
        station_runs: list[DispatchStationRun] | None = None,
        delivery_package_fields: dict[str, Any] | None = None,
        station_output_validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    ) -> GatehouseReport:
        """Run every deterministic check and return the aggregate report."""

        station_runs = station_runs or []
        delivery_package_fields = delivery_package_fields or {}
        checks = [
            self.check_manifest_completeness(manifest),
            self.check_route_station_schema(route),
            self.check_acceptance_criteria_presence(manifest),
            self.check_station_output_schema(
                station_runs, validators=station_output_validators
            ),
            self.check_permission_constraints(manifest),
            self.check_delivery_package_completeness(route, delivery_package_fields),
        ]
        return GatehouseReport(checks=checks)

    def evaluate_delivery(
        self,
        *,
        job_id: str,
        manifest: DispatchManifest,
        route: DispatchRoute,
        reviewer_result: ReviewerStationResult,
        report: GatehouseReport | None = None,
        station_runs: list[DispatchStationRun] | None = None,
        delivery_package_fields: dict[str, Any] | None = None,
        station_output_validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
        warning_decision_resolution: bool | None = None,
        route_uses_reviewer: bool = True,
        now: datetime | None = None,
    ) -> DeliveryPackage:
        """Combine structural checks + the reviewer verdict into a DeliveryPackage.

        Per the handoff table. ``warning_decision_resolution`` carries the
        user's accept(``True``)/reject(``False``) answer to a reviewer-``warning``
        decision; ``None`` means unresolved (a :class:`DispatchDecision` is
        created and the package is ``decision_required``). ``route_uses_reviewer``
        controls whether the cost note is attached (default ``True`` because
        this gate is only meaningful when a Reviewer Station ran).
        """

        if report is None:
            report = self.run_checks(
                manifest=manifest,
                route=route,
                station_runs=station_runs,
                delivery_package_fields=delivery_package_fields,
                station_output_validators=station_output_validators,
            )

        cost_note = REVIEWER_COST_NOTE if route_uses_reviewer else None

        # A failed deterministic check blocks regardless of the reviewer verdict:
        # the Gatehouse is the structural gate.
        if not report.passed:
            return DeliveryPackage(
                job_id=job_id,
                status=DeliveryStatus.BLOCKED,
                gatehouse_report=report,
                required_fixes=list(reviewer_result.required_fixes),
                cost_note=cost_note,
                reviewer_status=reviewer_result.status,
                summary=(
                    "Delivery Gate blocked on failed structural check(s): "
                    + ", ".join(c.name for c in report.failures)
                ),
            )

        status = reviewer_result.status

        if status is ReviewerStatus.PASS:
            return DeliveryPackage(
                job_id=job_id,
                status=DeliveryStatus.DELIVERY_READY,
                gatehouse_report=report,
                cost_note=cost_note,
                reviewer_status=status,
                summary="Reviewer pass; Delivery Gate proceeds.",
            )

        if status is ReviewerStatus.FAIL:
            # v0.1-alpha: blocked package carrying required_fixes, no repair loop.
            return DeliveryPackage(
                job_id=job_id,
                status=DeliveryStatus.BLOCKED,
                gatehouse_report=report,
                required_fixes=list(reviewer_result.required_fixes),
                cost_note=cost_note,
                reviewer_status=status,
                summary=(
                    "Reviewer fail; Delivery Gate blocked. "
                    "required_fixes attached; no automatic repair loop (v0.1-alpha)."
                ),
            )

        # status is WARNING → decision-gated.
        if warning_decision_resolution is None:
            decision = self._build_warning_decision(
                job_id=job_id, reviewer_result=reviewer_result, now=now
            )
            return DeliveryPackage(
                job_id=job_id,
                status=DeliveryStatus.DECISION_REQUIRED,
                gatehouse_report=report,
                decision=decision,
                cost_note=cost_note,
                reviewer_status=status,
                summary="Reviewer warning; user decision required (accept/reject).",
            )

        if warning_decision_resolution:
            return DeliveryPackage(
                job_id=job_id,
                status=DeliveryStatus.DELIVERY_READY_WITH_WARNING,
                gatehouse_report=report,
                cost_note=cost_note,
                reviewer_status=status,
                summary="Reviewer warning accepted by user; delivery ready with warning.",
            )

        return DeliveryPackage(
            job_id=job_id,
            status=DeliveryStatus.BLOCKED,
            gatehouse_report=report,
            required_fixes=list(reviewer_result.required_fixes),
            cost_note=cost_note,
            reviewer_status=status,
            summary="Reviewer warning rejected by user; delivery blocked.",
        )

    @staticmethod
    def _build_warning_decision(
        *,
        job_id: str,
        reviewer_result: ReviewerStationResult,
        now: datetime | None = None,
    ) -> DispatchDecision:
        """Create the accept/reject DispatchDecision for a reviewer ``warning``."""

        created_at = now or datetime.now(timezone.utc)
        reason = (
            reviewer_result.delivery_recommendation.reason
            or "Reviewer Station returned a warning."
        )
        return DispatchDecision(
            id=f"decision_{job_id}_reviewer_warning",
            job_id=job_id,
            created_at=created_at,
            scope=DecisionScope.JOB,
            title="Accept reviewer warning?",
            question=(
                "The Reviewer Station returned a warning. Accept the warning and "
                "deliver, or reject and block delivery?"
            ),
            reason=reason,
            risk_level=RiskLevel.MEDIUM,
            options=[
                DecisionOption(
                    id="accept",
                    label="Accept warning",
                    description="Deliver with warning (delivery_ready_with_warning).",
                    tradeoffs=["Ships work the reviewer flagged for attention."],
                ),
                DecisionOption(
                    id="reject",
                    label="Reject warning",
                    description="Block delivery; do not ship.",
                    tradeoffs=["Work is not delivered until the warning is addressed."],
                ),
            ],
            recommendation=DecisionRecommendation(
                option_id="accept",
                rationale="A warning is non-blocking; review the notes before accepting.",
            ),
            default_action=DecisionDefaultAction(
                option_id="accept", auto_apply_after=AutoApplyAfter.NEVER
            ),
            status=DecisionStatus.PENDING,
        )


__all__ = [
    "REVIEWER_COST_NOTE",
    "DeliveryStatus",
    "GatehouseCheckResult",
    "GatehouseReport",
    "DeliveryPackage",
    "Gatehouse",
]
