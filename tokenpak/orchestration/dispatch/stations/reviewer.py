"""Reviewer Station — semantic review via a single TIP LLM call.

The Reviewer Station validates **substance**: does the build/draft station's
output actually satisfy the manifest's acceptance criteria and constraints? It
is the counterpart to the deterministic Gatehouse (:mod:`..gatehouse`), which
validates **structure**. Two distinct contracts.

I/O contracts (:class:`ReviewerStationInput` / :class:`ReviewerStationResult`)
are the Reviewer Station's fixed contracts. The result's
``delivery_recommendation`` is **derived** from ``status`` (never authored
independently) via the single source-of-truth map :data:`STATUS_TO_DELIVERY`.

Runtime: the Reviewer goes "through TIP (single LLM call per review)". The
dispatch runtime is not built yet, so this module does NOT wire a real provider.
Instead it takes an *injected* client conforming to the :class:`ReviewerLLM`
protocol. :class:`ReviewerStation.review` builds the review prompt, makes
**exactly one** call via the injected client, schema-validates the response into
a :class:`ReviewerStationResult` (failing loud on malformed output), derives the
delivery recommendation, and returns the result. No automatic repair loop.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, Union, runtime_checkable

from pydantic import Field, ValidationError, model_validator

from ..models.artifact import DispatchArtifact
from ..models.common import AcceptanceCriterion, Constraint, DispatchBaseModel
from ..models.effect import DispatchEffect
from ..models.enums import (
    CriterionStatus,
    DeliveryRecommendationStatus,
    FixSeverity,
    ReviewerStatus,
    RiskLevel,
    SuggestedStation,
)

# ---------------------------------------------------------------------------
# Status → delivery-recommendation derivation (single source of truth)
# ---------------------------------------------------------------------------

# delivery_recommendation.status is DERIVED from
# ReviewerStationResult.status. This map is the ONLY place the derivation lives;
# both the result model and the Gatehouse handoff read from it so the two can
# never drift ("always dynamic": one source of truth, no duplicated table).
STATUS_TO_DELIVERY: dict[ReviewerStatus, DeliveryRecommendationStatus] = {
    ReviewerStatus.PASS: DeliveryRecommendationStatus.READY,
    ReviewerStatus.WARNING: DeliveryRecommendationStatus.READY_WITH_WARNING,
    ReviewerStatus.FAIL: DeliveryRecommendationStatus.BLOCKED,
}


def derive_delivery_status(status: ReviewerStatus) -> DeliveryRecommendationStatus:
    """Derive ``delivery_recommendation.status`` from the reviewer ``status``."""

    return STATUS_TO_DELIVERY[status]


# ---------------------------------------------------------------------------
# Reviewer Station I/O
# ---------------------------------------------------------------------------


class ReviewerStationInput(DispatchBaseModel):
    """Input to the Reviewer Station.

    ``artifacts`` carries :class:`DispatchArtifact` records (the schema
    names the field type ``Artifact``; the foundation's artifact record is
    ``DispatchArtifact``). ``effect_records`` carries the build station's
    :class:`DispatchEffect` records.
    """

    manifest_id: str
    route_id: str
    build_station_result_id: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    proposed_or_applied_patch: str | None = None
    effect_records: list[DispatchEffect] = Field(default_factory=list)
    artifacts: list[DispatchArtifact] = Field(default_factory=list)
    context_summary: str = ""
    known_risk_flags: list[str] = Field(default_factory=list)


class CriterionResult(DispatchBaseModel):
    """Per-acceptance-criterion review verdict."""

    criterion_id: str
    status: CriterionStatus
    notes: str = ""


class RequiredFix(DispatchBaseModel):
    """A fix the build must make before delivery."""

    severity: FixSeverity
    description: str
    suggested_station: SuggestedStation


class ReviewerRiskFlag(DispatchBaseModel):
    """A risk surfaced by the reviewer.

    ``id`` is registry-bound to the PAKPlan risk_flag registry (kept as a free
    string at v0.1-alpha; the registry itself is a separate concern).
    """

    id: str = Field(description="PAKPlan risk_flag registry id")
    severity: RiskLevel
    notes: str = ""


class DeliveryRecommendation(DispatchBaseModel):
    """Delivery recommendation — DERIVED from status."""

    status: DeliveryRecommendationStatus
    reason: str = ""


class ReviewerStationResult(DispatchBaseModel):
    """Output of the Reviewer Station.

    ``delivery_recommendation.status`` is **derived** from ``status`` and the
    model enforces that invariant at validation time: a result whose
    recommendation status does not match :data:`STATUS_TO_DELIVERY` is rejected
    fail-loud. Callers should let :meth:`for_status` build the recommendation
    rather than hand-author it.
    """

    status: ReviewerStatus
    criteria_results: list[CriterionResult] = Field(default_factory=list)
    required_fixes: list[RequiredFix] = Field(default_factory=list)
    risk_flags: list[ReviewerRiskFlag] = Field(default_factory=list)
    delivery_recommendation: DeliveryRecommendation

    @model_validator(mode="after")
    def _delivery_status_is_derived(self) -> "ReviewerStationResult":
        expected = STATUS_TO_DELIVERY[self.status]
        if self.delivery_recommendation.status is not expected:
            raise ValueError(
                "delivery_recommendation.status must be DERIVED from status: "
                f"status={self.status.value!r} requires "
                f"{expected.value!r}, got "
                f"{self.delivery_recommendation.status.value!r}."
            )
        return self

    @classmethod
    def for_status(
        cls,
        status: ReviewerStatus | str,
        *,
        criteria_results: list[CriterionResult] | None = None,
        required_fixes: list[RequiredFix] | None = None,
        risk_flags: list[ReviewerRiskFlag] | None = None,
        reason: str = "",
    ) -> "ReviewerStationResult":
        """Construct a result with the delivery recommendation derived from ``status``."""

        status_e = status if isinstance(status, ReviewerStatus) else ReviewerStatus(status)
        return cls(
            status=status_e,
            criteria_results=criteria_results or [],
            required_fixes=required_fixes or [],
            risk_flags=risk_flags or [],
            delivery_recommendation=DeliveryRecommendation(
                status=derive_delivery_status(status_e), reason=reason
            ),
        )


# ---------------------------------------------------------------------------
# Injected LLM client contract + the station runner
# ---------------------------------------------------------------------------


@runtime_checkable
class ReviewerLLM(Protocol):
    """Injected single-call review client (routes through TIP at runtime).

    The dispatch runtime (TIP worker invocation) is a later packet; the Reviewer
    Station depends only on this thin contract so it can be exercised with a fake
    client in tests and bound to the real TIP path once the runner lands. The
    callable takes the rendered review prompt and returns the model's raw output
    — either a JSON string or an already-parsed mapping. Exactly one call is made
    per review.
    """

    def __call__(self, prompt: str) -> Union[str, dict[str, Any]]: ...


class ReviewerOutputError(ValueError):
    """Raised when the injected client returns output that is not a valid result.

    Covers non-JSON strings, non-mapping payloads, and payloads that fail
    :class:`ReviewerStationResult` schema validation (including the derived
    delivery-recommendation invariant). Subclasses :class:`ValueError` so callers
    can catch it broadly while still matching it by exact type.
    """


# Stable template id so the prompt surface is greppable / versionable without a
# hardcoded prose blob duplicated elsewhere.
REVIEW_PROMPT_TEMPLATE_ID = "dispatch.reviewer.review.v1"


class ReviewerStation:
    """Semantic-review station: one TIP LLM call per review.

    Construct with an injected :class:`ReviewerLLM`; call :meth:`review` with a
    :class:`ReviewerStationInput`. The station builds the review prompt, makes
    **exactly one** client call, schema-validates the response into a
    :class:`ReviewerStationResult` (fail-loud on malformed output), and returns
    it with ``delivery_recommendation`` derived from ``status``. No repair loop.
    """

    template_id = REVIEW_PROMPT_TEMPLATE_ID

    def __init__(self, client: ReviewerLLM) -> None:
        self._client = client

    def build_prompt(self, payload: ReviewerStationInput) -> str:
        """Render the review prompt from the input (deterministic; no I/O).

        The prompt embeds the manifest/route identifiers, the acceptance
        criteria and constraints to check, the patch/artifacts under review, and
        the exact JSON shape the model must return — derived from the live
        result schema so the instruction can never drift from the contract.
        """

        result_schema = ReviewerStationResult.model_json_schema()
        criteria = [{"id": c.id, "description": c.description} for c in payload.acceptance_criteria]
        constraints = [{"id": c.id, "description": c.description} for c in payload.constraints]
        request = {
            "template_id": self.template_id,
            "task": (
                "Perform a semantic review of a build/draft station's output. "
                "Decide whether it satisfies each acceptance criterion and "
                "respects each constraint. Return ONLY a JSON object matching the "
                "provided result schema; do not include prose outside the JSON. "
                "Set status to 'pass', 'warning', or 'fail'. The "
                "delivery_recommendation.status MUST be derived from status: "
                "pass->ready, warning->ready_with_warning, fail->blocked."
            ),
            "manifest_id": payload.manifest_id,
            "route_id": payload.route_id,
            "build_station_result_id": payload.build_station_result_id,
            "acceptance_criteria": criteria,
            "constraints": constraints,
            "proposed_or_applied_patch": payload.proposed_or_applied_patch,
            "artifacts": [a.model_dump(mode="json") for a in payload.artifacts],
            "effect_records": [e.model_dump(mode="json") for e in payload.effect_records],
            "context_summary": payload.context_summary,
            "known_risk_flags": payload.known_risk_flags,
            "result_schema": result_schema,
        }
        return json.dumps(request, sort_keys=True)

    def review(self, payload: ReviewerStationInput) -> ReviewerStationResult:
        """Run one review: build prompt → one LLM call → parse/validate → derive.

        Makes exactly one call via the injected client. Raises
        :class:`ReviewerOutputError` if the response is not parseable into a
        schema-valid :class:`ReviewerStationResult` (the derived
        delivery-recommendation invariant is part of that validation).
        """

        prompt = self.build_prompt(payload)
        raw = self._client(prompt)  # exactly one LLM call per review
        parsed = self._coerce_to_mapping(raw)
        result = self._validate(parsed)
        # Defensive re-assertion of the derived invariant (the model validator
        # already enforces it; this keeps the derivation contract explicit at the
        # station boundary even if a future caller bypasses the model validator).
        expected = derive_delivery_status(result.status)
        if result.delivery_recommendation.status is not expected:  # pragma: no cover
            raise ReviewerOutputError(
                "reviewer output violated the derived delivery_recommendation "
                "invariant after validation."
            )
        return result

    @staticmethod
    def _coerce_to_mapping(raw: Union[str, dict[str, Any]]) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ReviewerOutputError(
                    f"reviewer client returned non-JSON output: {exc}"
                ) from exc
            if not isinstance(decoded, dict):
                raise ReviewerOutputError(
                    "reviewer client returned a JSON value that is not an object "
                    f"(got {type(decoded).__name__})."
                )
            return decoded
        raise ReviewerOutputError(
            f"reviewer client must return a JSON string or a mapping; got {type(raw).__name__}."
        )

    @staticmethod
    def _validate(parsed: dict[str, Any]) -> ReviewerStationResult:
        try:
            return ReviewerStationResult.model_validate(parsed)
        except ValidationError as exc:
            raise ReviewerOutputError(f"reviewer output failed schema validation: {exc}") from exc


__all__ = [
    "STATUS_TO_DELIVERY",
    "derive_delivery_status",
    "ReviewerStationInput",
    "CriterionResult",
    "RequiredFix",
    "ReviewerRiskFlag",
    "DeliveryRecommendation",
    "ReviewerStationResult",
    "ReviewerLLM",
    "ReviewerOutputError",
    "ReviewerStation",
    "ReviewerStatus",
    "REVIEW_PROMPT_TEMPLATE_ID",
]
