import pytest

pytest.importorskip(
    "tokenpak._internal.regression.feature_detector", reason="module not available in current build"
)
from tokenpak._internal.regression.feature_detector import (
    build_targeted_repair_prompt,
    detect_feature_regressions,
)


def test_detects_summary_scope_drift():
    baseline = (
        "We should recommend a low-risk migration plan with phased rollout and rollback checks."
    )
    candidate = "Completely different proposal about redesigning the UI animation and branding."

    report = detect_feature_regressions("summary", candidate, baseline)

    assert "scope" in report.features_drifted
    assert report.features_drifted["scope"].drift_magnitude > 0


def test_code_patch_detects_structure_change_pattern_drift():
    baseline = """+ def fix_bug():
+     assert True
- old_call()
+ new_call()
"""
    candidate = "+ minimal_one_line_change()"

    report = detect_feature_regressions("code_patch", candidate, baseline)

    assert "structure" in report.features_drifted
    assert "change_pattern" in report.features_drifted


def test_targeted_repair_prompt_preserves_non_drifted_features():
    baseline = "label: bug\nconfidence: 0.93\nreason: failing assertion path"
    candidate = "label: bug\nconfidence: 0.93\nreason: assertion path and stack trace"

    report = detect_feature_regressions(
        "classification",
        candidate,
        baseline,
        thresholds={"style": 0.4, "structure": 0.8, "length": 0.99},
    )
    prompt = build_targeted_repair_prompt("classification", candidate, baseline, report)

    assert "Repair only drifted features" in prompt
    assert "Preserve these non-drifted features as-is" in prompt
    assert "length" in prompt
