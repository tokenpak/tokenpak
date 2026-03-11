"""TokenPak regression detection package."""

from .artifact_reuse import (
    ArtifactReusePlan,
    ComponentComparison,
    compare_components,
    merge_artifact_components,
    plan_artifact_reuse,
    split_artifact_components,
    validate_merged_artifact,
)
from .feature_detector import (
    FeatureDetectionReport,
    FeatureResult,
    build_targeted_repair_prompt,
    detect_feature_regressions,
)
from .classifier import (
    DEFAULT_SIGNATURE_TYPE_MAP,
    FIX_PATHS,
    RegressionObservation,
    RegressionType,
    build_fix_plan,
    classify_regression_signatures,
    detect_regression_types,
)

__all__ = [
    "BaselineRegistry",
    "DeltaDetector",
    "DeltaResult",
    "StabilityScorer",
    "StabilityScore",
    "RunRecord",
    "compute_stability",
    "RetrievalQualityWatchdog",
    "QueryRetrievalRecord",
    "RetrievalAlert",
    "RetrievalBaseline",
    "FeatureResult",
    "FeatureDetectionReport",
    "detect_feature_regressions",
    "build_targeted_repair_prompt",
    "ComponentComparison",
    "ArtifactReusePlan",
    "split_artifact_components",
    "compare_components",
    "plan_artifact_reuse",
    "merge_artifact_components",
    "validate_merged_artifact",
    "RegressionType",
    "RegressionObservation",
    "FIX_PATHS",
    "DEFAULT_SIGNATURE_TYPE_MAP",
    "detect_regression_types",
    "classify_regression_signatures",
    "build_fix_plan",
]
