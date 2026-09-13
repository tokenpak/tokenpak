"""Reconstructed native text shapes never imply billing or comparison support."""

import hashlib
import json

import pytest

from tokenpak.proxy.spend_guard.request_tokens import observe_request, observe_response
from tokenpak.proxy.spend_guard.request_workload import observe_request as priced_request

from .native_text_fixtures import BETA, native_text_body

URL = "https://api.anthropic.com/v1/messages?beta=true"
HEADERS = {"Authorization": "Bearer synthetic", "anthropic-beta": BETA}


def request(body=None, url=URL):
    return observe_request(
        json.dumps(native_text_body() if body is None else body).encode(), url, HEADERS
    )


def complete(observed):
    raw = json.dumps(
        {
            "id": "msg_synthetic",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 8,
                "output_tokens": 2,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 3,
            },
        }
    ).encode()
    return observe_response(observed, raw, streaming=False, complete=True, status=200)


def test_captured_semantic_fields_need_a_complete_response_and_never_claim_prices():
    body = native_text_body()
    raw = json.dumps(body).encode()
    observed = request(body)
    assert observed.token_reason_codes == ("response_unavailable",)
    assert not observed.token_usage_complete
    observed = complete(observed)
    assert observed.token_usage_complete and observed.input_tokens == 15
    assert observed.body_sha256 == hashlib.sha256(raw).hexdigest()
    assert observed.feature_profile_status == "unclassified"
    assert observed.comparison_reason_codes == ("feature_profile_unclassified",)
    assert observed.actual_cost_usd is None and observed.billing_semantics == "unestablished"
    for private in ("synthetic work", "synthetic-account", "synthetic-device", BETA):
        assert private not in observed.to_json()
    assert {"route_unsupported", "headers_unsupported", "request_feature_unsupported"} <= set(
        priced_request(raw, URL, HEADERS).reason_codes
    )


def test_optional_absence_is_supported_but_not_silently_substituted_for_null():
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "text"}],
    }
    assert complete(request(body)).token_usage_complete
    for name in (
        "system",
        "stream",
        "tools",
        "metadata",
        "cache_control",
        "thinking",
        "output_config",
        "context_management",
    ):
        assert not complete(request({**body, name: None})).token_usage_complete, name


@pytest.mark.parametrize(
    "name,value",
    [
        ("thinking", {}),
        ("thinking", {"type": "adaptive"}),
        ("thinking", {"type": "adaptive", "display": True}),
        ("thinking", {"type": "adaptive", "display": "omitted", "budget_tokens": 1024}),
        ("output_config", {"effort": "low"}),
        ("output_config", {"effort": "high", "format": {}}),
        ("context_management", {"edits": []}),
        ("context_management", {"edits": [{"type": "clear_thinking_20251015", "keep": 1}]}),
        ("context_management", {"edits": [{"type": "unknown", "keep": "all"}]}),
        (
            "context_management",
            {"edits": [{"type": "clear_thinking_20251015", "keep": "all", "extra": 1}]},
        ),
        ("context_management", {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}] * 2}),
        (
            "context_management",
            {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}], "extra": None},
        ),
        ("metadata", {}),
        ("metadata", {"user_id": {}}),
        ("metadata", {"user_id": "x", "extra": "private"}),
        ("metadata", {"user_id": "x" * 4097}),
        ("tools", {}),
        ("tools", [{"name": "synthetic", "input_schema": {"type": "object"}}]),
        ("tool_choice", {"type": "none"}),
        ("stream", 1),
        ("max_tokens", True),
        ("max_tokens", 0),
        ("max_tokens", 2**54),
        ("unknown_native_feature", {}),
        ("cache_control", {"type": "ephemeral", "ttl": None}),
        ("cache_control", {"type": "ephemeral", "ttl": "1h", "scope": "global"}),
    ],
)
def test_unknown_or_malformed_present_features_refuse(name, value):
    body = native_text_body()
    body[name] = value
    observed = complete(request(body))
    assert not observed.token_usage_complete
    assert "request_feature_unsupported" in observed.token_reason_codes


@pytest.mark.parametrize(
    "content",
    [
        None,
        [],
        {"type": "text", "text": "x"},
        [{"type": "image", "source": {}}],
        [{"type": "tool_use", "id": "x", "name": "x", "input": {}}],
        [{"type": "tool_result", "tool_use_id": "x", "content": "x"}],
        [{"type": "thinking", "thinking": "x", "signature": "x"}],
        [{"type": "text", "text": "x", "citations": []}],
        [{"type": "text", "text": 1}],
        [{"type": "text", "text": "x", "cache_control": None}],
    ],
)
def test_only_typed_text_blocks_are_supported(content):
    body = native_text_body()
    body["messages"][0]["content"] = content
    observed = complete(request(body))
    assert "input_modality_unsupported" in observed.token_reason_codes
    assert not observed.token_usage_complete


@pytest.mark.parametrize(
    "suffix",
    [
        "?",
        "?beta",
        "?beta=",
        "?beta=True",
        "?beta=false",
        "?beta=true&beta=true",
        "?beta=true&extra=x",
        "?%62eta=true",
        "?beta=%74rue",
        "?beta=true&",
        "?beta=true#",
        "?beta=true\n",
        "?beta=true%00",
        "/",
        "#x",
    ],
)
def test_only_exact_native_query_forms_are_supported(suffix):
    observed = complete(request(url="https://api.anthropic.com/v1/messages" + suffix))
    assert "route_unsupported" in observed.token_reason_codes
    assert not observed.token_usage_complete


def test_feature_digest_binds_shapes_but_not_content_or_identifiers():
    original = native_text_body()
    changed = native_text_body()
    changed["messages"][0]["content"][0]["text"] = "different private text"
    changed["metadata"]["user_id"] = "different private identifier"
    first, second = request(original), request(changed)
    assert first.body_sha256 != second.body_sha256
    assert first.request_feature_profile_sha256 == second.request_feature_profile_sha256
    changed["max_tokens"] = 16000
    assert request(changed).request_feature_profile_sha256 != first.request_feature_profile_sha256
    changed = native_text_body()
    changed["system"][1]["cache_control"]["ttl"] = "5m"
    assert complete(request(changed)).token_usage_complete
    assert request(changed).request_feature_profile_sha256 != first.request_feature_profile_sha256


def test_body_depth_nodes_duplicates_and_unicode_are_bounded():
    bodies = [
        b'{"model":"x","model":"y"}',
        b'{"messages":NaN}',
        b'{"messages":"\\ud800"}',
        b'{"messages":' + b"[" * 18 + b"0" + b"]" * 18 + b"}",
        json.dumps({"messages": [None] * 100001}).encode(),
    ]
    for body in bodies:
        assert "request_invalid" in observe_request(body, URL, HEADERS).token_reason_codes
    assert (
        "request_unavailable"
        in observe_request(b" " * (8 * 1024 * 1024 + 1), URL, HEADERS).token_reason_codes
    )
