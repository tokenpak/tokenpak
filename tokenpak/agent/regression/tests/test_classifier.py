from tokenpak.agent.regression.classifier import (
    RegressionObservation,
    RegressionType,
    build_fix_plan,
    classify_regression_signatures,
    detect_regression_types,
)


def test_regression_type_count_is_eight() -> None:
    assert len(list(RegressionType)) == 8


def test_detects_format_verbosity_scope_and_cost() -> None:
    observation = RegressionObservation(
        schema_valid=False,
        missing_fields=2,
        response_length=310,
        baseline_response_length=200,
        files_touched=5,
        expected_files_touched=2,
        tokens_used=1300,
        baseline_tokens_avg=1000,
    )

    detected = detect_regression_types(observation)

    assert RegressionType.FORMAT in detected
    assert RegressionType.VERBOSITY in detected
    assert RegressionType.SCOPE in detected
    assert RegressionType.COST in detected


def test_detects_factuality_retrieval_ranking_and_latency() -> None:
    observation = RegressionObservation(
        ranking_shift=0.2,
        factuality_score=0.7,
        factuality_threshold=0.85,
        retrieval_overlap=0.55,
        retrieval_threshold=0.8,
        latency_ms=2500,
        baseline_latency_ms=1000,
    )

    detected = detect_regression_types(observation)

    assert RegressionType.RANKING in detected
    assert RegressionType.FACTUALITY in detected
    assert RegressionType.RETRIEVAL in detected
    assert RegressionType.LATENCY in detected


def test_classify_regression_signatures_maps_known_signatures() -> None:
    signatures = ["schema_mismatch", "token_spike", "unknown"]
    classified = classify_regression_signatures(signatures)

    assert classified["schema_mismatch"] == RegressionType.FORMAT
    assert classified["token_spike"] == RegressionType.COST
    assert "unknown" not in classified


def test_build_fix_plan_contains_narrow_paths() -> None:
    fix_plan = build_fix_plan({RegressionType.FORMAT, RegressionType.VERBOSITY, RegressionType.SCOPE, RegressionType.COST})

    assert "strict schema" in fix_plan[RegressionType.FORMAT].lower()
    assert "max-length" in fix_plan[RegressionType.VERBOSITY].lower()
    assert "narrower context" in fix_plan[RegressionType.SCOPE].lower()
    assert "cheaper model" in fix_plan[RegressionType.COST].lower()


def test_below_thresholds_do_not_trigger() -> None:
    observation = RegressionObservation(
        schema_valid=True,
        missing_fields=0,
        response_length=149,
        baseline_response_length=100,
        files_touched=2,
        expected_files_touched=2,
        tokens_used=119,
        baseline_tokens_avg=100,
        latency_ms=119,
        baseline_latency_ms=100,
    )

    detected = detect_regression_types(observation)
    assert detected == set()
