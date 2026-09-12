# SPDX-License-Identifier: Apache-2.0
"""Validate native text/thinking output without changing the priced parser."""

from __future__ import annotations

from .request_token_shape import _bounded
from .request_workload import _MAX_BYTES, _json, _response


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(
        optional
    ):
        raise ValueError("unsupported response fields")


def _string(value, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value):
        raise ValueError("invalid response string")
    value.encode("utf-8")


def _block(block, *, streaming=False):
    if not isinstance(block, dict):
        raise ValueError("invalid content block")
    kind = block.get("type")
    if kind == "text":
        _object(block, {"type", "text"}, {"citations"})
        _string(block["text"])
        if block.get("citations") not in (None, []):
            raise ValueError("unsupported citations")
    elif kind == "thinking":
        _object(block, {"type", "thinking", "signature"})
        _string(block["thinking"])
        _string(block["signature"], nonempty=not streaming)
    elif kind == "redacted_thinking":
        _object(block, {"type", "data"})
        _string(block["data"], nonempty=True)
    else:
        raise ValueError("unsupported response modality")
    return kind


def _stop_fields(value, *, unfinished=False):
    reason, sequence = value["stop_reason"], value["stop_sequence"]
    if reason is None and unfinished:
        if sequence is not None:
            raise ValueError("stop sequence without a stop reason")
        return False
    if type(reason) is not str or reason not in (
        "end_turn",
        "max_tokens",
        "stop_sequence",
        "refusal",
    ):
        raise ValueError("unsupported response stop reason")
    if reason == "stop_sequence":
        _string(sequence, nonempty=True)
    elif sequence is not None:
        raise ValueError("stop sequence contradicts stop reason")
    return True


def _message(message, *, streaming=False):
    _object(
        message,
        {"id", "type", "role", "model", "usage", "content", "stop_reason", "stop_sequence"},
        {"container"},
    )
    _string(message["id"], nonempty=True)
    # Observation bound, not a claim about the provider's maximum identifier.
    if len(message["id"].encode("utf-8")) > 512 or any(
        ord(c) < 32 or ord(c) == 127 for c in message["id"]
    ):
        raise ValueError("unsupported message identifier")
    if message["type"] != "message" or message["role"] != "assistant":
        raise ValueError("invalid message envelope")
    if message.get("container") is not None:
        raise ValueError("unsupported response container")
    stopped = _stop_fields(message, unfinished=streaming)
    content = message["content"]
    if not isinstance(content, list) or (streaming and content):
        raise ValueError("invalid message content")
    if streaming and stopped:
        raise ValueError("terminal message start")
    for block in content:
        _block(block)


def _events(raw):
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if "\r" in text or not text.endswith("\n\n"):
        raise ValueError("incomplete event framing")
    for frame in text.split("\n\n"):
        data, names = [], []
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, colon, value = line.partition(":")
            if field not in ("data", "event") or not colon:
                raise ValueError("unsupported event framing")
            (data if field == "data" else names).append(value.removeprefix(" "))
        if not data:
            if names:
                raise ValueError("event without data")
            continue
        event = _json("\n".join(data))
        _bounded(event)
        if (
            not isinstance(event, dict)
            or len(names) > 1
            or (names and names[0] != event.get("type"))
        ):
            raise ValueError("ambiguous event type")
        yield event


def token_response(raw: bytes, streaming: bool):
    """Return model/usage only after block types and lifecycle are supported.

    Thinking signatures and redacted data are type-checked transiently; their
    opaque bytes are never persisted or interpreted as a new output modality.
    """
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise ValueError("invalid response size")
    if not streaming:
        message = _json(raw)
        _bounded(message)
        _message(message)
        return message["model"], message["usage"]
    started = terminal = final_delta = message_deltas = False
    active = None
    index = 0
    signature = False
    for event in _events(raw):
        kind = event.get("type")
        if terminal:
            raise ValueError("event after completion")
        if kind == "ping":
            _object(event, {"type"})
            continue
        if kind == "message_start":
            _object(event, {"type", "message"})
            if started:
                raise ValueError("multiple messages")
            _message(event["message"], streaming=True)
            started = True
            continue
        if not started:
            raise ValueError("event before message")
        if kind == "content_block_start":
            _object(event, {"type", "index", "content_block"})
            if (
                active is not None
                or message_deltas
                or type(event["index"]) is not int
                or event["index"] != index
            ):
                raise ValueError("invalid content block order")
            active = _block(event["content_block"], streaming=True)
            signature = active == "thinking" and bool(event["content_block"]["signature"])
        elif kind in ("content_block_delta", "content_block_stop"):
            _object(
                event, {"type", "index", "delta"} if kind.endswith("delta") else {"type", "index"}
            )
            if active is None or type(event["index"]) is not int or event["index"] != index:
                raise ValueError("unmatched content block event")
            if kind == "content_block_stop":
                if active == "thinking" and not signature:
                    raise ValueError("thinking signature unavailable")
                active = None
                index += 1
                continue
            delta = event["delta"]
            if not isinstance(delta, dict):
                raise ValueError("invalid block delta")
            subtype = delta.get("type")
            if active == "text" and subtype == "text_delta":
                _object(delta, {"type", "text"})
                _string(delta["text"])
            elif (
                active == "thinking"
                and not signature
                and subtype in ("thinking_delta", "signature_delta")
            ):
                field = "thinking" if subtype == "thinking_delta" else "signature"
                _object(delta, {"type", field})
                _string(delta[field], nonempty=field == "signature")
                signature = field == "signature"
            else:
                raise ValueError("unsupported block delta")
        elif kind == "message_delta":
            _object(event, {"type", "usage", "delta"})
            if active is not None or final_delta:
                raise ValueError("message delta before content completion")
            _object(event["delta"], {"stop_reason", "stop_sequence"})
            final_delta = _stop_fields(event["delta"], unfinished=True)
            message_deltas = True
        elif kind == "message_stop":
            _object(event, {"type"})
            if active is not None or not final_delta:
                raise ValueError("message stopped before completion")
            terminal = True
        else:
            raise ValueError("unsupported response event")
    if not terminal:
        raise ValueError("incomplete response stream")
    # Keep the established cumulative-usage, count and model parser untouched.
    # Its Unicode splitlines limitation can still make otherwise supported
    # streams unavailable; that failure never grants complete token coverage.
    return _response(raw, True)
