from tokenpak._internal.regression.artifact_reuse import (
    merge_artifact_components,
    plan_artifact_reuse,
    validate_merged_artifact,
)


def test_reuses_retrieval_set_and_regenerates_stale_chunk_only():
    baseline = {
        "retrieval_set": ["chunk-a", "chunk-b", "chunk-c"],
        "structure": "overview -> details -> risks",
        "facts": ["f1", "f2"],
        "code": "def run():\n    return True",
    }
    candidate = {
        "retrieval_set": ["chunk-a", "chunk-b", "chunk-c"],
        "structure": "overview -> details -> risks",
        "facts": ["f1", "stale-fact"],
        "code": "def run():\n    return True",
    }

    plan = plan_artifact_reuse(candidate, baseline)

    assert "retrieval_set" in plan.reuse_components
    assert "structure" in plan.reuse_components
    assert "code" in plan.reuse_components
    assert "facts" in plan.regenerate_components


def test_keeps_summary_structure_and_regenerates_facts_only():
    baseline = {
        "structure": "summary\nkey points\nnext actions",
        "facts": "Revenue grew 12% QoQ with 2.1% churn",
    }
    candidate = {
        "structure": "summary\nkey points\nnext actions",
        "facts": "Revenue changed a lot and churn maybe improved",
    }

    plan = plan_artifact_reuse(candidate, baseline)

    assert "structure" in plan.reuse_components
    assert "facts" in plan.regenerate_components


def test_keeps_code_patch_skeleton_repairs_one_function():
    baseline = {
        "code": "def skeleton():\n    return repair_target(x)",
        "facts": ["target function must validate nulls"],
    }
    candidate = {
        "code": "def skeleton():\n    return repair_target(x)",
        "facts": ["stale fact"],
    }

    plan = plan_artifact_reuse(candidate, baseline)

    merged = merge_artifact_components(
        plan,
        regenerated_components={"facts": ["target function must validate nulls"]},
    )
    ok, _ = validate_merged_artifact(merged, baseline)

    assert "code" in plan.reuse_components
    assert ok is True


def test_merge_validation_fails_when_regenerated_component_not_fixed():
    baseline = {
        "retrieval_set": ["a", "b", "c"],
        "facts": ["alpha", "beta"],
    }
    candidate = {
        "retrieval_set": ["a", "b", "c"],
        "facts": ["stale"],
    }

    plan = plan_artifact_reuse(candidate, baseline)
    merged = merge_artifact_components(plan, regenerated_components={"facts": ["still stale"]})
    ok, comparisons = validate_merged_artifact(merged, baseline)

    assert ok is False
    assert comparisons["facts"].regressed is True
