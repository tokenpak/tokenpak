from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RegressionType(str, Enum):
    FORMAT = "format"
    VERBOSITY = "verbosity"
    RANKING = "ranking"
    FACTUALITY = "factuality"
    SCOPE = "scope"
    RETRIEVAL = "retrieval"
    COST = "cost"
    LATENCY = "latency"


FIX_PATHS: dict[RegressionType, str] = {
    RegressionType.FORMAT: "Re-prompt with strict schema only; reject non-schema output.",
    RegressionType.VERBOSITY: "Re-prompt with explicit max-length constraint.",
    RegressionType.RANKING: "Lock deterministic ranking features and stable tie-break rules.",
    RegressionType.FACTUALITY: "Increase grounding constraints and require source-backed claims.",
    RegressionType.SCOPE: "Reject patch and rerun with narrower context/file scope.",
    RegressionType.RETRIEVAL: "Refresh retrieval chunks and enforce higher overlap threshold.",
    RegressionType.COST: "Tighten token budget and route to cheaper model tier.",
    RegressionType.LATENCY: "Use faster preset and reduce expensive retrieval/model hops.",
}


@dataclass(frozen=True)
class RegressionObservation:
    schema_valid: bool = True
    missing_fields: int = 0
    response_length: int = 0
    baseline_response_length: int = 0
    files_touched: int = 0
    expected_files_touched: int = 0
    tokens_used: int = 0
    baseline_tokens_avg: int = 0
    ranking_shift: float = 0.0
    factuality_score: float = 1.0
    factuality_threshold: float = 0.85
    retrieval_overlap: float = 1.0
    retrieval_threshold: float = 0.8
    latency_ms: int = 0
    baseline_latency_ms: int = 0


DEFAULT_SIGNATURE_TYPE_MAP: dict[str, RegressionType] = {
    "schema_mismatch": RegressionType.FORMAT,
    "missing_required_field": RegressionType.FORMAT,
    "response_too_long": RegressionType.VERBOSITY,
    "ranking_flip": RegressionType.RANKING,
    "unsupported_claim": RegressionType.FACTUALITY,
    "patch_scope_overrun": RegressionType.SCOPE,
    "low_retrieval_overlap": RegressionType.RETRIEVAL,
    "token_spike": RegressionType.COST,
    "latency_spike": RegressionType.LATENCY,
}


def detect_regression_types(observation: RegressionObservation) -> set[RegressionType]:
    detected: set[RegressionType] = set()

    if not observation.schema_valid or observation.missing_fields > 0:
        detected.add(RegressionType.FORMAT)

    if observation.baseline_response_length > 0 and observation.response_length > int(
        observation.baseline_response_length * 1.5
    ):
        detected.add(RegressionType.VERBOSITY)

    if observation.ranking_shift > 0:
        detected.add(RegressionType.RANKING)

    if observation.factuality_score < observation.factuality_threshold:
        detected.add(RegressionType.FACTUALITY)

    if (
        observation.expected_files_touched > 0
        and observation.files_touched > observation.expected_files_touched
    ):
        detected.add(RegressionType.SCOPE)

    if observation.retrieval_overlap < observation.retrieval_threshold:
        detected.add(RegressionType.RETRIEVAL)

    if observation.baseline_tokens_avg > 0 and observation.tokens_used > int(
        observation.baseline_tokens_avg * 1.2
    ):
        detected.add(RegressionType.COST)

    if observation.baseline_latency_ms > 0 and observation.latency_ms > int(
        observation.baseline_latency_ms * 1.2
    ):
        detected.add(RegressionType.LATENCY)

    return detected


def classify_regression_signatures(
    signatures: list[str],
    signature_map: dict[str, RegressionType] | None = None,
) -> dict[str, RegressionType]:
    mapping = signature_map or DEFAULT_SIGNATURE_TYPE_MAP
    classified: dict[str, RegressionType] = {}

    for signature in signatures:
        if signature in mapping:
            classified[signature] = mapping[signature]

    return classified


def build_fix_plan(regression_types: set[RegressionType]) -> dict[RegressionType, str]:
    return {
        regression_type: FIX_PATHS[regression_type]
        for regression_type in sorted(regression_types, key=lambda t: t.value)
    }
