"""Synthetic wire observations must retain unknown, adverse and private facts."""

import json
from dataclasses import replace

import pytest

from tokenpak.proxy.spend_guard.request_workload import (
    RequestWorkload,
    observe_request,
    observe_response,
)


def request(**updates):
    data = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "private text"}],
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
    }
    data.update(updates)
    return observe_request(
        json.dumps(data).encode(),
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": "private-key"},
    )


def response(observed=None, **usage_updates):
    usage = {
        "input_tokens": 10,
        "output_tokens": 7,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 3,
        "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0},
        "service_tier": "standard",
        "inference_geo": "global",
    }
    usage.update(usage_updates)
    raw = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "usage": usage,
            "content": [{"type": "text", "text": "private answer"}],
        }
    ).encode()
    return observe_response(observed or request(), raw, streaming=False, complete=True, status=200)


def test_complete_direct_workload_roundtrip_is_content_free_and_disjoint():
    observed = response()
    assert observed.reason_codes == ()
    assert (observed.input_tokens, observed.output_tokens) == (33, 7)
    assert (observed.cache_read_tokens, observed.cache_write_tokens) == (20, 3)
    assert observed.cache_ttl_seconds == 300
    raw = observed.to_json()
    assert RequestWorkload.from_json(raw) == observed
    for private in ("private text", "private answer", "private-key", "https", "/v1/messages"):
        assert private not in raw


@pytest.mark.parametrize(
    "url",
    [
        "http://api.anthropic.com/v1/messages",
        "https://example.com/v1/messages",
        "https://api.anthropic.com.evil/v1/messages",
        "https://api.anthropic.com/v1/messages?beta=1",
        "https://api.anthropic.com:444/v1/messages",
        "https://user@api.anthropic.com/v1/messages",
        "https://api.anthropic.com/v1/messages/batches",
        "https://api.openai.com/v1/responses",
    ],
)
def test_custom_partner_or_ambiguous_route_is_unavailable(url):
    observed = observe_request(b'{"model":"test"}', url, {"x-api-key": "secret"})
    assert observed.provider is None
    assert "route_unsupported" in observed.reason_codes
    assert url not in observed.to_json()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "secret"},
        {"x-api-key": "secret", "anthropic-beta": "beta"},
        {"x-api-key": "a", "X-Api-Key": "b"},
        {"x-api-key": "secret", "content-encoding": "gzip"},
    ],
)
def test_header_modes_do_not_turn_into_direct_standard_api_evidence(headers):
    observed = observe_request(b"{}", "https://api.anthropic.com/v1/messages", headers)
    assert "headers_unsupported" in observed.reason_codes
    assert "secret" not in observed.to_json()


@pytest.mark.parametrize(
    "payload",
    [b'{"model":"x","model":"y"}', b"[]", b"{", b'{"messages":[{"x":1,"x":2}]}', b'{"x":NaN}'],
)
def test_invalid_json_is_observed_as_unavailable(payload):
    observed = observe_request(payload, "https://api.anthropic.com/v1/messages", {"x-api-key": "x"})
    assert "request_invalid" in observed.reason_codes


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"speed": "fast"}, "request_feature_unsupported"),
        ({"tools": [{"type": "web_search_20250305"}]}, "request_feature_unsupported"),
        (
            {"messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]},
            "input_modality_unsupported",
        ),
        ({"service_tier": "priority"}, "request_feature_unsupported"),
        ({"inference_geo": "unknown"}, "request_feature_unsupported"),
        ({"model": "private/path"}, "model_unavailable"),
        ({"cache_control": None}, "request_invalid"),
    ],
)
def test_unsupported_request_facts_are_not_usable(updates, reason):
    assert reason in response(request(**updates)).reason_codes


def test_missing_or_mixed_ttl_does_not_select_cheaper_band():
    missing = observe_request(
        b'{"model":"x","messages":[{"role":"user","content":"x"}]}',
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": "x"},
    )
    assert missing.cache_ttl_seconds is None
    assert "cache_ttl_unavailable" in missing.reason_codes
    mixed = request(
        system=[{"type": "text", "text": "x", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    )
    assert mixed.cache_ttl_seconds is None
    assert "cache_ttl_mixed" in mixed.reason_codes
    hour = response(
        request(cache_control={"type": "ephemeral", "ttl": "1h"}),
        cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3},
    )
    assert hour.reason_codes == () and hour.cache_ttl_seconds == 3600


@pytest.mark.parametrize(
    "field,reason",
    [
        ("service_tier", "service_tier_unavailable"),
        ("inference_geo", "region_unavailable"),
        ("input_tokens", "usage_unavailable"),
        ("output_tokens", "usage_unavailable"),
        ("cache_read_input_tokens", "usage_unavailable"),
        ("cache_creation_input_tokens", "usage_unavailable"),
    ],
)
def test_missing_response_fact_remains_missing(field, reason):
    assert reason in response(**{field: None}).reason_codes


@pytest.mark.parametrize("value", [True, -1, 1.5, "7", 2**54, float("inf")])
def test_bad_counts_cannot_become_observed_zero(value):
    observed = response(output_tokens=value)
    assert observed.reason_codes
    assert observed.output_tokens is None


def test_assigned_tier_and_region_cannot_be_inferred_from_request_defaults():
    assigned = response(service_tier="priority", inference_geo="us")
    assert assigned.reason_codes == ()
    assert (assigned.service_tier, assigned.region) == ("priority", "us")
    explicit = request(service_tier="standard_only", inference_geo="global")
    mismatch = response(explicit, service_tier="priority", inference_geo="us")
    assert set(mismatch.reason_codes) == {"region_mismatch", "service_tier_mismatch"}


@pytest.mark.parametrize(
    "updates",
    [
        {"cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3}},
        {"cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 0}},
        {"server_tool_use": {"web_search_requests": 1}},
        {"new_billable_feature": 1},
    ],
)
def test_extra_charges_and_cache_disagreement_remain_unavailable(updates):
    assert response(**updates).reason_codes


def test_request_digest_binds_exact_bytes_not_reserialized_json():
    a = observe_request(b'{"model":"x"}', "", {})
    b = observe_request(b'{ "model": "x" }', "", {})
    assert a.body_sha256 != b.body_sha256


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "native-request-workload/99"},
        {"extra": "private prompt"},
        {"reason_codes": [], "cache_ttl_seconds": None},
        {"response_complete": 1},
        {"cache_read_tokens": True},
        {"reason_codes": ["private-error-text"]},
        {"input_tokens": 1},
        {"model": "wrong-model"},
    ],
)
def test_stored_schema_is_strict_even_when_it_claims_usability(changes):
    data = response().to_dict()
    data.update(changes)
    with pytest.raises(ValueError):
        RequestWorkload.from_json(json.dumps(data))


def test_partial_or_model_mismatched_response_cannot_become_usable():
    raw = b'{"model":"different","usage":{}}'
    result = observe_response(request(), raw, streaming=False, complete=False, status=200)
    assert "response_model_mismatch" in result.reason_codes
    assert "response_incomplete" in result.reason_codes


def test_stream_requires_complete_strict_cumulative_usage():
    usage = {
        "input_tokens": 10,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "service_tier": "standard",
        "inference_geo": "global",
    }
    events = [
        {"type": "message_start", "message": {"model": "claude-sonnet-4-6", "usage": usage}},
        {"type": "message_delta", "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    raw = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    result = observe_response(request(), raw, streaming=True, complete=True, status=200)
    assert result.reason_codes == () and result.output_tokens == 7
    for altered in (
        raw.replace(b"7}", b"true}"),
        raw + b"data: {}\n\n",
        raw.replace(b'data: {"type": "message_stop"}\n\n', b""),
        raw.replace(b"7}", b'7,"output_tokens":3}'),
    ):
        assert observe_response(
            request(), altered, streaming=True, complete=True, status=200
        ).reason_codes


def test_complete_observation_cannot_drop_required_fields():
    with pytest.raises(ValueError):
        replace(response(), response_complete=False)


def test_exponent_overflow_and_unknown_cache_charge_are_unavailable():
    observed = observe_request(b'{"model":"x","temperature":1e999}', "", {})
    assert "request_invalid" in observed.reason_codes
    assert (
        "request_feature_unsupported"
        in response(request(output_config={"format": {"type": "image"}})).reason_codes
    )
    assert (
        "response_feature_unsupported"
        in response(
            cache_creation={
                "ephemeral_5m_input_tokens": 3,
                "ephemeral_1h_input_tokens": 0,
                "extra_cache_charge": 7,
            }
        ).reason_codes
    )
