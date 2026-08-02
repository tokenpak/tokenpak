"""Compatibility and wire-shape checks for telemetry-local DTOs."""

from __future__ import annotations

from datetime import datetime

from tokenpak.core.contracts.trace import Recommendation as ContractRecommendation
from tokenpak.core.contracts.trace import StageTrace as ContractStageTrace
from tokenpak.telemetry import pipeline_trace, recommendations


def test_pipeline_stage_keeps_historical_runtime_identity():
    # Historical module path + class name are compatibility surface for
    # reflection and default pickle globals across releases.
    assert pipeline_trace.StageTrace.__module__ == "tokenpak.telemetry.pipeline_trace"
    assert pipeline_trace.StageTrace.__qualname__ == "StageTrace"
    assert pipeline_trace.StageTrace is not ContractStageTrace

    stage = pipeline_trace.StageTrace(
        name="capsule",
        enabled=True,
        input_tokens=10,
        output_tokens=7,
        tokens_delta=-3,
        details={"source": "test"},
        duration_ms=1.25,
    )
    trace = pipeline_trace.PipelineTrace(
        request_id="r-1",
        timestamp=datetime(2026, 8, 1),
        input_tokens=10,
        stages=[stage],
        output_tokens=7,
        tokens_saved=3,
        cost_saved=0.1,
    )

    assert trace.to_dict()["stages"] == [
        {
            "name": "capsule",
            "enabled": True,
            "input_tokens": 10,
            "output_tokens": 7,
            "tokens_delta": -3,
            "duration_ms": 1.25,
            "details": {"source": "test"},
        }
    ]


def test_ranked_recommendation_keeps_historical_runtime_identity():
    # Historical module path + class name are compatibility surface for
    # reflection and default pickle globals across releases.
    assert recommendations.Recommendation.__module__ == "tokenpak.telemetry.recommendations"
    assert recommendations.Recommendation.__qualname__ == "Recommendation"
    assert recommendations.Recommendation is not ContractRecommendation

    recommendation = recommendations.Recommendation(
        id="cache.lookup",
        severity="high",
        title="Enable cache lookups",
        evidence={"lookups": 0},
        action="Enable the cache.",
        expected="Lookups become nonzero.",
    )
    assert recommendation.to_dict() == {
        "id": "cache.lookup",
        "severity": "high",
        "title": "Enable cache lookups",
        "evidence": {"lookups": 0},
        "action": "Enable the cache.",
        "expected": "Lookups become nonzero.",
    }
