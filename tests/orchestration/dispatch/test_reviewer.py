"""Tests for the Reviewer Station (Standards Delta v0 §5.7).

Verifies, with a FAKE injected client (no real LLM):

  * each reviewer status path (pass / warning / fail) parses + validates;
  * the I/O contracts match §5.7 (status + criteria + required_fixes + risk_flags
    + derived delivery_recommendation);
  * malformed output fails loud (non-JSON, non-object, schema-invalid, and a
    delivery_recommendation that is not derived from status);
  * delivery_recommendation is derived from status via the single source-of-truth
    map;
  * exactly one client call is made per review.
"""

from __future__ import annotations

import json

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.models.enums import (
    DeliveryRecommendationStatus,
    ReviewerStatus,
)
from tokenpak.orchestration.dispatch.stations.reviewer import (
    STATUS_TO_DELIVERY,
    CriterionResult,
    DeliveryRecommendation,
    RequiredFix,
    ReviewerOutputError,
    ReviewerStation,
    ReviewerStationInput,
    ReviewerStationResult,
    derive_delivery_status,
)

# ---------------------------------------------------------------------------
# Fake injected client
# ---------------------------------------------------------------------------


class _FakeReviewerLLM:
    """Records call count and returns a canned payload (str or dict)."""

    def __init__(self, payload, *, as_json_string: bool = True):
        self._payload = payload
        self._as_json_string = as_json_string
        self.calls = 0
        self.last_prompt = None

    def __call__(self, prompt: str):
        self.calls += 1
        self.last_prompt = prompt
        if isinstance(self._payload, (str, bytes)):
            return self._payload
        if self._as_json_string:
            return json.dumps(self._payload)
        return self._payload


def _input() -> ReviewerStationInput:
    return ReviewerStationInput(
        manifest_id="manifest_01",
        route_id="route.code_task.v1",
        build_station_result_id="stationrun_01",
        acceptance_criteria=[{"id": "ac1", "description": "adds the flag"}],
        constraints=[{"id": "c1", "description": "no new deps"}],
        proposed_or_applied_patch="--- a\n+++ b\n",
        context_summary="built the flag",
        known_risk_flags=["touches_cli"],
    )


def _result_payload(status: str) -> dict:
    """A schema-valid reviewer payload with delivery_recommendation derived."""

    derived = STATUS_TO_DELIVERY[ReviewerStatus(status)].value
    payload = {
        "status": status,
        "criteria_results": [{"criterion_id": "ac1", "status": "pass", "notes": "ok"}],
        "required_fixes": [],
        "risk_flags": [],
        "delivery_recommendation": {"status": derived, "reason": f"{status} verdict"},
    }
    return payload


# ---------------------------------------------------------------------------
# Status paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_delivery",
    [
        ("pass", DeliveryRecommendationStatus.READY),
        ("warning", DeliveryRecommendationStatus.READY_WITH_WARNING),
        ("fail", DeliveryRecommendationStatus.BLOCKED),
    ],
)
def test_reviewer_status_paths(status, expected_delivery):
    client = _FakeReviewerLLM(_result_payload(status))
    station = ReviewerStation(client)

    result = station.review(_input())

    assert isinstance(result, ReviewerStationResult)
    assert result.status is ReviewerStatus(status)
    assert result.delivery_recommendation.status is expected_delivery
    # delivery_recommendation is DERIVED from status.
    assert result.delivery_recommendation.status is derive_delivery_status(result.status)
    # Exactly one LLM call per review (§5.7).
    assert client.calls == 1


def test_reviewer_fail_carries_required_fixes():
    payload = _result_payload("fail")
    payload["criteria_results"] = [
        {"criterion_id": "ac1", "status": "fail", "notes": "flag missing"}
    ]
    payload["required_fixes"] = [
        {
            "severity": "high",
            "description": "implement the flag",
            "suggested_station": "build",
        }
    ]
    client = _FakeReviewerLLM(payload)

    result = ReviewerStation(client).review(_input())

    assert result.status is ReviewerStatus.FAIL
    assert result.delivery_recommendation.status is DeliveryRecommendationStatus.BLOCKED
    assert len(result.required_fixes) == 1
    fix = result.required_fixes[0]
    assert isinstance(fix, RequiredFix)
    assert fix.severity.value == "high"
    assert fix.suggested_station.value == "build"
    assert client.calls == 1


def test_reviewer_accepts_dict_payload_without_json_encoding():
    # Client may return an already-parsed mapping rather than a JSON string.
    client = _FakeReviewerLLM(_result_payload("pass"), as_json_string=False)
    result = ReviewerStation(client).review(_input())
    assert result.status is ReviewerStatus.PASS
    assert client.calls == 1


# ---------------------------------------------------------------------------
# Exactly-one-call discipline
# ---------------------------------------------------------------------------


def test_review_makes_exactly_one_client_call():
    client = _FakeReviewerLLM(_result_payload("pass"))
    station = ReviewerStation(client)
    station.review(_input())
    assert client.calls == 1
    # A second review is a second call — not amortized/cached.
    station.review(_input())
    assert client.calls == 2


def test_build_prompt_does_not_call_the_client():
    client = _FakeReviewerLLM(_result_payload("pass"))
    station = ReviewerStation(client)
    prompt = station.build_prompt(_input())
    assert client.calls == 0
    # Prompt embeds the contract identifiers and the result schema.
    assert "manifest_01" in prompt
    assert "result_schema" in prompt


# ---------------------------------------------------------------------------
# Malformed output → fail loud
# ---------------------------------------------------------------------------


def test_malformed_non_json_string_fails_loud():
    client = _FakeReviewerLLM("this is not json")
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())
    assert client.calls == 1


def test_malformed_json_array_not_object_fails_loud():
    client = _FakeReviewerLLM("[1, 2, 3]")
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())


def test_schema_invalid_payload_fails_loud():
    # Unknown extra field → extra='forbid' validation error → ReviewerOutputError.
    payload = _result_payload("pass")
    payload["bogus_field"] = "nope"
    client = _FakeReviewerLLM(payload)
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())


def test_missing_required_field_fails_loud():
    payload = _result_payload("pass")
    del payload["delivery_recommendation"]
    client = _FakeReviewerLLM(payload)
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())


def test_underived_delivery_recommendation_fails_loud():
    # status=fail but delivery_recommendation.status=ready → violates the §5.7
    # derivation invariant → fail loud.
    payload = _result_payload("fail")
    payload["delivery_recommendation"] = {"status": "ready", "reason": "wrong"}
    client = _FakeReviewerLLM(payload)
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())


def test_non_str_non_dict_client_output_fails_loud():
    client = _FakeReviewerLLM(12345, as_json_string=False)
    with pytest.raises(ReviewerOutputError):
        ReviewerStation(client).review(_input())


# ---------------------------------------------------------------------------
# Result model: derived-invariant + for_status helper
# ---------------------------------------------------------------------------


def test_result_model_rejects_handauthored_mismatch():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ReviewerStationResult(
            status=ReviewerStatus.PASS,
            delivery_recommendation=DeliveryRecommendation(
                status=DeliveryRecommendationStatus.BLOCKED
            ),
        )


@pytest.mark.parametrize("status", ["pass", "warning", "fail"])
def test_for_status_derives_recommendation(status):
    result = ReviewerStationResult.for_status(
        status,
        criteria_results=[CriterionResult(criterion_id="ac1", status="pass")],
        reason="r",
    )
    assert result.delivery_recommendation.status is derive_delivery_status(ReviewerStatus(status))
    assert result.delivery_recommendation.reason == "r"
