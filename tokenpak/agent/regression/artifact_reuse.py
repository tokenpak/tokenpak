from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_COMPONENT_KEYS = ("retrieval_set", "structure", "facts", "code")


@dataclass(frozen=True)
class ComponentComparison:
    name: str
    baseline: Any
    candidate: Any
    similarity: float
    regressed: bool


@dataclass(frozen=True)
class ArtifactReusePlan:
    reuse_components: dict[str, Any]
    regenerate_components: dict[str, Any]
    comparisons: dict[str, ComponentComparison]



def _normalize_component(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        normalized = [_normalize_component(v) for v in value]
        if all(isinstance(v, str) for v in normalized):
            return sorted(normalized)
        return normalized
    if isinstance(value, dict):
        return {k: _normalize_component(v) for k, v in sorted(value.items(), key=lambda kv: kv[0])}
    return value



def _component_similarity(candidate: Any, baseline: Any) -> float:
    c = _normalize_component(candidate)
    b = _normalize_component(baseline)

    if isinstance(c, str) and isinstance(b, str):
        if not c and not b:
            return 1.0
        c_tokens = set(c.lower().split())
        b_tokens = set(b.lower().split())
        if not c_tokens and not b_tokens:
            return 1.0
        union = c_tokens | b_tokens
        if not union:
            return 1.0
        return len(c_tokens & b_tokens) / len(union)

    if isinstance(c, list) and isinstance(b, list):
        c_set = {str(v) for v in c}
        b_set = {str(v) for v in b}
        if not c_set and not b_set:
            return 1.0
        union = c_set | b_set
        if not union:
            return 1.0
        return len(c_set & b_set) / len(union)

    return 1.0 if c == b else 0.0



def split_artifact_components(artifact: dict[str, Any]) -> dict[str, Any]:
    components: dict[str, Any] = {}
    for key in _COMPONENT_KEYS:
        if key in artifact:
            components[key] = artifact[key]
    return components



def compare_components(
    candidate_components: dict[str, Any],
    baseline_components: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, ComponentComparison]:
    merged_thresholds = {
        "retrieval_set": 0.8,
        "structure": 0.75,
        "facts": 0.85,
        "code": 0.85,
        **(thresholds or {}),
    }

    comparisons: dict[str, ComponentComparison] = {}
    for key in _COMPONENT_KEYS:
        baseline = baseline_components.get(key)
        candidate = candidate_components.get(key)
        similarity = _component_similarity(candidate, baseline)
        threshold = merged_thresholds.get(key, 0.8)
        regressed = similarity < threshold
        comparisons[key] = ComponentComparison(
            name=key,
            baseline=baseline,
            candidate=candidate,
            similarity=similarity,
            regressed=regressed,
        )

    return comparisons



def plan_artifact_reuse(
    candidate_artifact: dict[str, Any],
    baseline_artifact: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> ArtifactReusePlan:
    candidate_components = split_artifact_components(candidate_artifact)
    baseline_components = split_artifact_components(baseline_artifact)
    comparisons = compare_components(candidate_components, baseline_components, thresholds=thresholds)

    reuse_components: dict[str, Any] = {}
    regenerate_components: dict[str, Any] = {}

    for key, comparison in comparisons.items():
        if comparison.regressed:
            regenerate_components[key] = comparison.baseline
        else:
            reuse_components[key] = comparison.candidate

    return ArtifactReusePlan(
        reuse_components=reuse_components,
        regenerate_components=regenerate_components,
        comparisons=comparisons,
    )



def merge_artifact_components(
    plan: ArtifactReusePlan,
    regenerated_components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(plan.reuse_components)
    regenerated = regenerated_components or {}

    for key, fallback in plan.regenerate_components.items():
        merged[key] = regenerated.get(key, fallback)

    return merged



def validate_merged_artifact(
    merged_artifact: dict[str, Any],
    baseline_artifact: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> tuple[bool, dict[str, ComponentComparison]]:
    comparisons = compare_components(merged_artifact, baseline_artifact, thresholds=thresholds)
    ok = all(not c.regressed for c in comparisons.values())
    return ok, comparisons
