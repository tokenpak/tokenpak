"""Supported native response shapes, separate from the legacy priced parser."""

import copy
import json

import pytest

from tokenpak.proxy.spend_guard.request_tokens import observe_request, observe_response

MODEL = "claude-sonnet-4-6"
USAGE = dict(
    input_tokens=10, output_tokens=7, cache_read_input_tokens=0, cache_creation_input_tokens=0
)
REQUEST = json.dumps(
    {
        "model": MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "synthetic"}],
        "thinking": {"type": "adaptive", "display": "omitted"},
    }
).encode()


def observe(raw, streaming=False):
    request = observe_request(
        REQUEST, "https://api.anthropic.com/v1/messages", {"x-api-key": "synthetic"}
    )
    return observe_response(request, raw, streaming=streaming, complete=True, status=200)


def message(content):
    return dict(
        id="msg_synthetic",
        type="message",
        role="assistant",
        model=MODEL,
        content=content,
        usage=USAGE,
        stop_reason="end_turn",
        stop_sequence=None,
    )


def events(block=None, deltas=()):
    start = message([])
    start.update(usage={**USAGE, "output_tokens": 0}, stop_reason=None)
    result = [{"type": "message_start", "message": start}]
    if block is not None:
        result.append({"type": "content_block_start", "index": 0, "content_block": block})
        result.extend(
            {"type": "content_block_delta", "index": 0, "delta": delta} for delta in deltas
        )
        result.append({"type": "content_block_stop", "index": 0})
    result.extend(
        [
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        ]
    )
    return result


def encoded(items):
    return b"".join(
        ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()
        for event in items
    )


@pytest.mark.parametrize(
    "block",
    [
        {"type": "text", "text": "synthetic"},
        {"type": "text", "text": "", "citations": None},
        {"type": "text", "text": "", "citations": []},
        {"type": "thinking", "thinking": "", "signature": "synthetic-signature"},
        {"type": "thinking", "thinking": "synthetic", "signature": "synthetic-signature"},
        {"type": "redacted_thinking", "data": "synthetic-redacted"},
    ],
)
def test_supported_json_text_and_thinking_preserve_token_facts_without_content(block):
    result = observe(json.dumps(message([block])).encode())
    assert result.token_usage_complete
    assert result.output_modality == "text" and result.output_tokens == 7
    assert result.actual_cost_usd is None and result.comparison_reason_codes
    assert "synthetic-signature" not in result.to_json()
    assert "synthetic-redacted" not in result.to_json()


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "url", "url": "https://invalid.test"}},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
        {"type": "unknown"},
        {"type": "text", "text": None},
        {"type": "text", "text": 1},
        {"type": "text", "text": "x", "citations": [{"type": "url"}]},
        {"type": "text", "text": "x", "unknown": True},
        {"type": "thinking", "thinking": "", "signature": ""},
        {"type": "thinking", "thinking": "", "signature": None},
        {"type": "thinking", "signature": "x"},
        {"type": "redacted_thinking", "data": ""},
        {"type": "redacted_thinking", "data": {}},
        None,
        "opaque",
    ],
)
def test_opaque_or_malformed_json_output_is_never_complete_text(block):
    result = observe(json.dumps(message([block])).encode())
    assert not result.token_usage_complete
    assert result.output_modality is None and "response_invalid" in result.token_reason_codes


@pytest.mark.parametrize(
    "change",
    [
        {"content": None},
        {"content": {}},
        {"role": "user"},
        {"type": "error"},
        {"stop_reason": "tool_use"},
        {"stop_reason": "pause_turn"},
        {"container": {"id": "x"}},
        {"unexpected_modality": "opaque"},
    ],
)
def test_unsupported_message_envelope_refuses(change):
    value = message([])
    value.update(change)
    assert not observe(json.dumps(value).encode()).token_usage_complete


def test_missing_content_is_not_observed_empty_content():
    value = message([])
    del value["content"]
    assert not observe(json.dumps(value).encode()).token_usage_complete


@pytest.mark.parametrize(
    "block,deltas",
    [
        ({"type": "text", "text": ""}, [{"type": "text_delta", "text": "synthetic"}]),
        (
            {"type": "thinking", "thinking": "", "signature": ""},
            [{"type": "signature_delta", "signature": "synthetic-signature"}],
        ),
        (
            {"type": "thinking", "thinking": "", "signature": ""},
            [
                {"type": "thinking_delta", "thinking": "synthetic"},
                {"type": "signature_delta", "signature": "synthetic-signature"},
            ],
        ),
        ({"type": "redacted_thinking", "data": "synthetic-redacted"}, []),
    ],
)
def test_supported_streams_include_omitted_and_summarized_thinking(block, deltas):
    result = observe(encoded(events(block, deltas)), True)
    assert result.token_usage_complete
    assert result.output_modality == "text"
    assert "synthetic-signature" not in result.to_json()


@pytest.mark.parametrize(
    "delta",
    [
        {"type": "input_json_delta", "partial_json": "{}"},
        {"type": "text_delta", "text": 1},
        {"type": "text_delta", "text": "x", "extra": True},
        {"type": "thinking_delta", "thinking": "x"},
        None,
    ],
)
def test_text_block_rejects_unsupported_deltas(delta):
    assert not observe(
        encoded(events({"type": "text", "text": ""}, [delta])), True
    ).token_usage_complete


@pytest.mark.parametrize("index", [True, -1, 1, "0", None])
def test_block_index_is_exact_contiguous_integer(index):
    value = events({"type": "text", "text": ""})
    value[1]["index"] = index
    assert not observe(encoded(value), True).token_usage_complete


def invalid_sequences():
    base = events({"type": "text", "text": ""}, [{"type": "text_delta", "text": "x"}])
    value = copy.deepcopy(base)
    value[2]["index"] = 1
    yield value
    yield base[:1] + base[2:]  # Delta without a block.
    yield base[:3] + base[4:]  # Missing block stop.
    yield base[:4] + [base[3]] + base[4:]  # Duplicate stop.
    yield base[:2] + [base[1]] + base[2:]  # Duplicate start.
    yield base[:4] + base[1:]  # Reused index.
    yield base[:1] + [base[0]] + base[1:]  # Duplicate message start.
    yield base[:-1]  # Missing message stop.
    yield base + [base[2]]  # Content after terminal.
    yield base[:4] + [base[-1]]  # Missing final usage delta.
    yield base[:4] + [base[-2], base[1], base[-1]]  # Block after final usage.
    yield base[1:]  # Content before message.
    value = copy.deepcopy(base)
    value[0]["message"]["content"] = [{"type": "text", "text": "initial"}]
    yield value


@pytest.mark.parametrize("value", list(invalid_sequences()))
def test_inconsistent_stream_lifecycle_refuses(value):
    assert not observe(encoded(value), True).token_usage_complete


@pytest.mark.parametrize(
    "deltas",
    [
        [],
        [{"type": "signature_delta", "signature": ""}],
        [
            {"type": "signature_delta", "signature": "x"},
            {"type": "thinking_delta", "thinking": "late"},
        ],
        [
            {"type": "signature_delta", "signature": "x"},
            {"type": "signature_delta", "signature": "again"},
        ],
    ],
)
def test_thinking_requires_one_terminal_signature(deltas):
    block = {"type": "thinking", "thinking": "", "signature": ""}
    assert not observe(encoded(events(block, deltas)), True).token_usage_complete


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "url", "url": "https://invalid.test"}},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
        {"type": "fallback", "model": "other"},
    ],
)
def test_opaque_stream_block_refuses(block):
    assert not observe(encoded(events(block)), True).token_usage_complete


def test_redacted_block_cannot_receive_an_unclassified_delta():
    value = events(
        {"type": "redacted_thinking", "data": "x"}, [{"type": "text_delta", "text": "x"}]
    )
    assert not observe(encoded(value), True).token_usage_complete


@pytest.mark.parametrize(
    "transform",
    [
        lambda raw: raw.replace(b"event: message_start", b"event: ping", 1),
        lambda raw: raw.replace(
            b"event: message_start", b"event: message_start\nevent: message_start", 1
        ),
        lambda raw: raw + b"event: ping\n\n",
        lambda raw: raw + b"garbage\n\n",
        lambda raw: raw[:-1],
        lambda raw: raw.replace(b'"index": 0', b'"index": 0, "index": 0', 1),
    ],
)
def test_ambiguous_or_incomplete_sse_framing_refuses(transform):
    raw = encoded(events({"type": "text", "text": ""}))
    assert not observe(transform(raw), True).token_usage_complete


def test_data_only_crlf_and_comment_ping_frames_are_supported():
    items = events({"type": "text", "text": ""})
    raw = b": keepalive\n\ndata: " + json.dumps({"type": "ping"}).encode() + b"\n\n"
    raw += b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in items)
    assert observe(raw.replace(b"\n", b"\r\n"), True).token_usage_complete


def test_supported_output_does_not_relax_legacy_priced_parser():
    from tokenpak.proxy.spend_guard.request_workload import _response

    raw = json.dumps(
        message([{"type": "image", "source": {"type": "url", "url": "https://invalid.test"}}])
    ).encode()
    assert _response(raw, False) == (MODEL, USAGE)
    assert not observe(raw).token_usage_complete


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "field", ["id", "type", "role", "model", "usage", "content", "stop_reason", "stop_sequence"]
)
def test_native_envelope_requires_explicit_fields(streaming, field):
    if streaming:
        items = events()
        del items[0]["message"][field]
        raw = encoded(items)
    else:
        value = message([])
        del value[field]
        raw = json.dumps(value).encode()
    assert not observe(raw, streaming).token_usage_complete


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("identifier", [None, True, 1, "", "x" * 513, "\n", "x\x7f", "é" * 257])
def test_native_identifier_is_a_bounded_string(streaming, identifier):
    if streaming:
        items = events()
        items[0]["message"]["id"] = identifier
        raw = encoded(items)
    else:
        value = message([])
        value["id"] = identifier
        raw = json.dumps(value).encode()
    assert not observe(raw, streaming).token_usage_complete


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "reason,sequence",
    [
        (None, None),
        (True, None),
        ("", None),
        ("unknown", None),
        ("end_turn", "unexpected"),
        ("max_tokens", "unexpected"),
        ("refusal", "unexpected"),
        ("stop_sequence", None),
        ("stop_sequence", ""),
        ("stop_sequence", True),
    ],
)
def test_incomplete_or_inconsistent_terminal_metadata_never_completes(streaming, reason, sequence):
    stop = {"stop_reason": reason, "stop_sequence": sequence}
    if streaming:
        items = events()
        items[-2]["delta"] = stop
        raw = encoded(items)
    else:
        value = message([])
        value.update(stop)
        raw = json.dumps(value).encode()
    assert not observe(raw, streaming).token_usage_complete


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "reason,sequence",
    [
        ("end_turn", None),
        ("max_tokens", None),
        ("refusal", None),
        ("stop_sequence", "synthetic-stop"),
    ],
)
def test_supported_terminal_reasons_establish_request_facts_only(streaming, reason, sequence):
    stop = {"stop_reason": reason, "stop_sequence": sequence}
    if streaming:
        items = events(
            {"type": "thinking", "thinking": "", "signature": ""},
            [{"type": "signature_delta", "signature": "synthetic-signature"}],
        )
        items[-2]["delta"] = stop
        raw = encoded(items)
    else:
        value = message([{"type": "thinking", "thinking": "", "signature": "synthetic-signature"}])
        value.update(stop)
        raw = json.dumps(value).encode()
    result = observe(raw, streaming)
    assert result.token_usage_complete and result.response_complete
    assert result.actual_cost_usd is None and result.comparison_reason_codes


@pytest.mark.parametrize("field", ["delta", "stop_reason", "stop_sequence"])
def test_stream_terminal_metadata_cannot_be_omitted(field):
    items = events()
    if field == "delta":
        del items[-2][field]
    else:
        del items[-2]["delta"][field]
    assert not observe(encoded(items), True).token_usage_complete


def test_nonterminal_metadata_requires_a_later_validated_terminal():
    items = events()
    progress = {
        "type": "message_delta",
        "usage": {"output_tokens": 3},
        "delta": {"stop_reason": None, "stop_sequence": None},
    }
    items.insert(-2, progress)
    assert observe(encoded(items), True).token_usage_complete
    del items[-2]
    assert not observe(encoded(items), True).token_usage_complete


def test_metadata_cannot_change_after_a_terminal_delta():
    items = events()
    items.insert(
        -1,
        {
            "type": "message_delta",
            "usage": {"output_tokens": 8},
            "delta": {"stop_reason": "refusal", "stop_sequence": None},
        },
    )
    assert not observe(encoded(items), True).token_usage_complete


@pytest.mark.parametrize("reason,sequence", [("end_turn", None), (None, "early")])
def test_message_start_is_explicitly_nonterminal(reason, sequence):
    items = events()
    items[0]["message"].update(stop_reason=reason, stop_sequence=sequence)
    assert not observe(encoded(items), True).token_usage_complete


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_inherited_unicode_stream_limitation_stays_unavailable(separator):
    items = events(
        {"type": "text", "text": ""}, [{"type": "text_delta", "text": "x" + separator + "y"}]
    )
    raw = b"".join(
        b"data: " + json.dumps(item, ensure_ascii=False).encode() + b"\n\n" for item in items
    )
    from tokenpak.proxy.spend_guard.response_token_shape import _events

    # The token shape parser uses native LF framing. The unchanged cumulative
    # usage parser still treats these Unicode characters as line separators.
    assert list(_events(raw)) == items
    result = observe(raw, True)
    assert not result.token_usage_complete and "response_invalid" in result.token_reason_codes
