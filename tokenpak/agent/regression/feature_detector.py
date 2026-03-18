from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureResult:
    name: str
    score: float
    threshold: float

    @property
    def drift_magnitude(self) -> float:
        return max(0.0, self.threshold - self.score)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


@dataclass(frozen=True)
class FeatureDetectionReport:
    artifact_type: str
    features_passed: dict[str, FeatureResult]
    features_drifted: dict[str, FeatureResult]


def _tokenize(text: str) -> list[str]:
    return [t for t in text.lower().split() if t]


def _safe_ratio(a: int, b: int) -> float:
    if b <= 0:
        return 1.0 if a <= 0 else 0.0
    return min(a, b) / max(a, b)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _contains_keywords(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    lower = text.lower()
    hits = sum(1 for k in keywords if k.lower() in lower)
    return hits / len(keywords)


def _measure_summary_features(candidate: str, baseline: str) -> dict[str, float]:
    c_tokens = _tokenize(candidate)
    b_tokens = _tokenize(baseline)

    return {
        "length": _safe_ratio(len(c_tokens), len(b_tokens)),
        "tone": _contains_keywords(candidate, ["should", "must", "recommend", "risk"]),
        "scope": _jaccard(set(c_tokens), set(b_tokens)),
    }


def _measure_code_patch_features(candidate: str, baseline: str) -> dict[str, float]:
    c_lines = candidate.splitlines()
    b_lines = baseline.splitlines()

    c_added = sum(1 for ln in c_lines if ln.strip().startswith("+"))
    b_added = sum(1 for ln in b_lines if ln.strip().startswith("+"))
    c_removed = sum(1 for ln in c_lines if ln.strip().startswith("-"))
    b_removed = sum(1 for ln in b_lines if ln.strip().startswith("-"))

    return {
        "structure": _safe_ratio(len(c_lines), len(b_lines)),
        "change_pattern": (_safe_ratio(c_added, b_added) + _safe_ratio(c_removed, b_removed)) / 2,
        "factual_grounding": _contains_keywords(candidate, ["test", "assert", "fix", "error"]),
    }


def _measure_classification_features(candidate: str, baseline: str) -> dict[str, float]:
    c_tokens = _tokenize(candidate)
    b_tokens = _tokenize(baseline)
    return {
        "style": _contains_keywords(candidate, ["label", "confidence", "reason"]),
        "structure": _contains_keywords(candidate, ["label:", "confidence:", "reason:"]),
        "length": _safe_ratio(len(c_tokens), len(b_tokens)),
    }


_MEASURERS: dict[str, Any] = {
    "summary": _measure_summary_features,
    "code_patch": _measure_code_patch_features,
    "classification": _measure_classification_features,
}

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "tone": 0.5,
    "structure": 0.55,
    "length": 0.6,
    "factual_grounding": 0.5,
    "style": 0.5,
    "scope": 0.45,
    "change_pattern": 0.55,
}


def detect_feature_regressions(
    artifact_type: str,
    candidate_text: str,
    baseline_text: str,
    thresholds: dict[str, float] | None = None,
) -> FeatureDetectionReport:
    if artifact_type not in _MEASURERS:
        raise ValueError(f"Unsupported artifact type: {artifact_type}")

    scores = _MEASURERS[artifact_type](candidate_text, baseline_text)
    merged_thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}

    passed: dict[str, FeatureResult] = {}
    drifted: dict[str, FeatureResult] = {}
    for feature, score in scores.items():
        threshold = merged_thresholds.get(feature, 0.5)
        result = FeatureResult(name=feature, score=score, threshold=threshold)
        if result.passed:
            passed[feature] = result
        else:
            drifted[feature] = result

    return FeatureDetectionReport(
        artifact_type=artifact_type,
        features_passed=passed,
        features_drifted=drifted,
    )


def build_targeted_repair_prompt(
    artifact_type: str,
    candidate_text: str,
    baseline_text: str,
    report: FeatureDetectionReport,
) -> str:
    if not report.features_drifted:
        return (
            "No feature drift detected. Preserve current artifact output exactly; "
            "no repair is required."
        )

    drift_list = sorted(report.features_drifted.values(), key=lambda x: x.drift_magnitude, reverse=True)
    drift_lines = "\n".join(
        f"- {item.name}: drift={item.drift_magnitude:.3f} (score={item.score:.3f}, threshold={item.threshold:.3f})"
        for item in drift_list
    )
    preserved = ", ".join(sorted(report.features_passed.keys())) or "none"

    return (
        f"Artifact type: {artifact_type}\n"
        "Repair only drifted features. Do not rewrite untouched dimensions.\n"
        "Drifted features:\n"
        f"{drift_lines}\n"
        f"Preserve these non-drifted features as-is: {preserved}.\n\n"
        "Candidate artifact:\n"
        f"{candidate_text}\n\n"
        "Baseline reference:\n"
        f"{baseline_text}\n"
    )
