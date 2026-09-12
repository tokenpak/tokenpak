"""Native counts remain observed without becoming price or comparison evidence."""

import json
from dataclasses import replace

import pytest

from tokenpak.proxy.spend_guard.request_tokens import (
    RequestTokenObservation,
    observe_request,
    observe_response,
)
from tokenpak.proxy.spend_guard.request_workload import observe_request as old_request

BODY = b'{"model":"claude-sonnet-4-6","max_tokens":64,"messages":[{"role":"user","content":"private text"}]}'
URL = "https://api.anthropic.com/v1/messages"


def request(headers=None, **kwargs):
    return observe_request(
        BODY, URL, headers or {"Authorization": "Bearer private-secret"}, **kwargs
    )


def response(observed=None, **updates):
    usage = {
        "input_tokens": 10,
        "output_tokens": 7,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 3,
        "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0},
    }
    usage.update(updates)
    raw = json.dumps({"model": "claude-sonnet-4-6", "usage": usage}).encode()
    return observe_response(observed or request(), raw, streaming=False, complete=True, status=200)


def test_provider_counts_survive_missing_prices_without_billing_or_comparison_claim():
    observed = response()
    assert observed.token_usage_complete
    assert (observed.uncached_input_tokens, observed.input_tokens, observed.output_tokens) == (
        10,
        33,
        7,
    )
    assert (observed.cache_read_tokens, observed.cache_creation_tokens) == (20, 3)
    assert observed.actual_cost_usd is None
    assert observed.billing_semantics == "unestablished"
    assert observed.authentication_kind == "forwarded_bearer_unclassified"
    assert observed.authentication_provenance == "final_forwarded_headers"
    assert observed.comparison_reason_codes == ("feature_profile_unclassified",)
    raw = observed.to_json()
    assert RequestTokenObservation.from_json(raw) == observed
    for private in ("private-secret", "private text", "Authorization", URL):
        assert private not in raw


def test_known_oauth_is_bound_to_router_metadata_not_a_client_claim():
    observed = response(request(credential_kind="oauth"))
    assert observed.authentication_kind == "oauth_bearer"
    assert observed.authentication_provenance == "credential_router_metadata"
    assert observed.billing_semantics == "unestablished"
    with pytest.raises(ValueError, match="OAuth ownership"):
        replace(observed, authentication_provenance="final_forwarded_headers")
    forged = request({"Authorization": "Bearer x", "X-Credential-Kind": "oauth"})
    assert forged.authentication_kind == "forwarded_bearer_unclassified"


def test_new_observer_does_not_relax_existing_priced_workload():
    headers = {"Authorization": "Bearer x", "anthropic-beta": "fixture-beta"}
    assert "headers_unsupported" in old_request(BODY, URL, headers).reason_codes
    observed = response(request(headers))
    assert observed.token_usage_complete
    assert observed.comparison_reason_codes
    assert "fixture-beta" not in observed.to_json()
    assert observed.request_feature_profile_sha256 != response().request_feature_profile_sha256
    with pytest.raises(ValueError):
        from tokenpak.proxy.spend_guard.request_pricing import price_request

        price_request(observed)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.anthropic.com/v1/messages",
        URL + "?x=1",
        "https://gateway.invalid/v1/messages",
        URL + "/batches",
    ],
)
def test_wrong_final_route_cannot_become_complete(url):
    assert not response(
        observe_request(BODY, url, {"Authorization": "Bearer x"})
    ).token_usage_complete


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic x"},
        {"Authorization": "Bearer x", "authorization": "Bearer y"},
        {"Authorization": "Bearer x", "x-api-key": "x"},
        {"x-api-key": "x", "content-encoding": "gzip"},
        {"Authorization": "Bearer x", "anthropic-beta": "x" * 2049},
    ],
)
def test_ambiguous_or_unsupported_authentication_stays_unavailable(headers):
    observed = response(observe_request(BODY, URL, headers))
    assert "headers_unsupported" in observed.token_reason_codes


@pytest.mark.parametrize("value", [None, True, -1, 1.1, "7", 2**54])
def test_invalid_counts_never_become_observed_zero(value):
    observed = response(output_tokens=value)
    assert not observed.token_usage_complete
    assert observed.output_tokens is None


@pytest.mark.parametrize(
    "updates",
    [
        {"cache_creation": None},
        {"cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 0}},
        {"server_tool_use": {"searches": 1}},
        {"new_usage_category": 1},
        {"output_tokens_details": {"reasoning_tokens": 8}},
    ],
)
def test_unknown_or_contradictory_usage_is_not_complete(updates):
    assert not response(**updates).token_usage_complete


def test_positive_creation_without_ttl_partition_remains_observed_only():
    from tokenpak.proxy.spend_guard.request_workload import observe_response as old_response

    usage = {
        "input_tokens": 10,
        "output_tokens": 7,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 3,
    }
    raw = json.dumps({"model": "claude-sonnet-4-6", "usage": usage}).encode()
    observed = observe_response(request(), raw, streaming=False, complete=True, status=200)
    assert observed.token_usage_complete
    assert observed.cache_creation_tokens == 3 and observed.input_tokens == 33
    assert observed.actual_cost_usd is None and observed.comparison_reason_codes
    legacy = old_response(
        old_request(BODY, URL, {"x-api-key": "x"}), raw, streaming=False, complete=True, status=200
    )
    assert "cache_usage_mismatch" in legacy.reason_codes


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "native-request-token-observation/2"},
        {"extra": "private text"},
        {"input_tokens": 1},
        {"token_usage_complete": 1},
        {"response_complete": False},
        {"actual_cost_usd": 0},
        {"billing_semantics": "subscription"},
        {"comparison_reason_codes": []},
        {"feature_profile_status": "supported_text"},
        {"token_reason_codes": ["private error"]},
        {"response_model": "other"},
    ],
)
def test_stored_completeness_and_claims_cannot_be_forged(change):
    data = response().to_dict()
    data.update(change)
    with pytest.raises(ValueError):
        RequestTokenObservation.from_json(json.dumps(data))


def test_native_stream_needs_full_terminal_and_monotonic_usage():
    usage = {
        "input_tokens": 10,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    events = [
        {"type": "message_start", "message": {"model": "claude-sonnet-4-6", "usage": usage}},
        {"type": "message_delta", "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    raw = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    assert observe_response(
        request(), raw, streaming=True, complete=True, status=200
    ).token_usage_complete
    for invalid in (
        raw.rsplit(b"data:", 1)[0],
        raw + b"data: {}\n\n",
        raw.replace(b"7}", b"true}"),
    ):
        assert not observe_response(
            request(), invalid, streaming=True, complete=True, status=200
        ).token_usage_complete


def test_strict_json_and_model_binding_are_preserved():
    for raw in (b'{"model":"x","model":"y"}', b'{"x":NaN}', b"[1]", b"{"):
        assert "request_invalid" in observe_request(raw, URL, {"x-api-key": "x"}).token_reason_codes
    result = observe_response(
        request(), b'{"model":"other","usage":{}}', streaming=False, complete=True, status=200
    )
    assert "response_model_mismatch" in result.token_reason_codes
    assert (
        observe_request(BODY + b" ", URL, {"x-api-key": "x"}).body_sha256 != request().body_sha256
    )


def test_receipt_bound_and_duplicate_fields_are_strict():
    with pytest.raises(ValueError):
        RequestTokenObservation.from_json(" " * 4097)
    with pytest.raises(ValueError):
        RequestTokenObservation.from_json(
            response()
            .to_json()
            .replace('"actual_cost_usd":null', '"actual_cost_usd":null,"actual_cost_usd":0')
        )
