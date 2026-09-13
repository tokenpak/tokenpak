# SPDX-License-Identifier: Apache-2.0
"""Bounded native text request shapes, independent of price applicability."""

from __future__ import annotations

import hashlib
import re

from .request_workload import _MAX_BYTES, _count, _json, _model

_MAX_DEPTH = 16
_MAX_NODES = 100000
_MAX_METADATA_BYTES = 4096
_ROUTE = re.compile(r"https://api\.anthropic\.com(?::443)?/v1/messages(?:\?beta=true)?\Z")
_FIELDS = {
    "model",
    "max_tokens",
    "messages",
    "system",
    "stream",
    "tools",
    "metadata",
    "cache_control",
    "thinking",
    "output_config",
    "context_management",
}


def _bounded(value) -> None:
    pending = [(value, 0)]
    seen = 0
    while pending:
        node, depth = pending.pop()
        seen += 1
        if seen > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError("request structure exceeds observation bounds")
        if isinstance(node, (dict, list)) and seen + len(pending) + len(node) > _MAX_NODES:
            raise ValueError("request node count exceeds observation bounds")
        if isinstance(node, dict):
            for key, child in node.items():
                key.encode("utf-8")
                pending.append((child, depth + 1))
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)
        elif isinstance(node, str):
            node.encode("utf-8")


def _cache(value):
    if (
        not isinstance(value, dict)
        or set(value) not in ({"type"}, {"type", "ttl"})
        or value.get("type") != "ephemeral"
        or value.get("ttl", "5m") not in ("5m", "1h")
    ):
        raise ValueError("unsupported cache directive")
    return {"type": "ephemeral", "ttl": value.get("ttl", "omitted")}


def _text(value):
    if isinstance(value, str):
        return "text_string"
    if not isinstance(value, list) or not value:
        raise ValueError("text content required")
    shape = []
    for block in value:
        if (
            not isinstance(block, dict)
            or set(block) not in ({"type", "text"}, {"type", "text", "cache_control"})
            or block.get("type") != "text"
            or not isinstance(block.get("text"), str)
        ):
            raise ValueError("only typed text blocks are supported")
        item = {"type": "text"}
        if "cache_control" in block:
            item["cache_control"] = _cache(block["cache_control"])
        shape.append(item)
    return shape


def observe_shape(body: bytes | None, url: str | None) -> tuple[dict, set[str], dict]:
    """Return only fixed facts, reasons and a content-free shape for hashing."""
    reasons = {"response_unavailable"}
    facts, profile = {}, {"schema": "native-text-request-shape/1"}
    if not isinstance(body, bytes) or not body or len(body) > _MAX_BYTES:
        return facts, reasons | {"request_unavailable"}, profile
    facts["body_sha256"] = hashlib.sha256(body).hexdigest()
    if isinstance(url, str) and _ROUTE.fullmatch(url):
        facts.update(provider="anthropic", transport="anthropic_messages_https")
        profile["query"] = "beta=true" if url.endswith("?beta=true") else "absent"
    else:
        reasons.add("route_unsupported")
    try:
        data = _json(body)
        if not isinstance(data, dict):
            raise ValueError("request object required")
        _bounded(data)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return facts, reasons | {"request_invalid"}, profile
    model = _model(data.get("model"))
    facts["request_model"] = model
    if model is None:
        reasons.add("model_unavailable")
    try:
        if set(data) - _FIELDS or not _count(data.get("max_tokens")):
            raise ValueError("unsupported request fields or output bound")
        profile["fields"] = sorted(data)
        profile["max_tokens"] = data["max_tokens"]
        if "stream" in data:
            if type(data["stream"]) is not bool:
                raise ValueError("stream must be a boolean")
            profile["stream"] = data["stream"]
        if "tools" in data:
            if type(data["tools"]) is not list or data["tools"]:
                raise ValueError("only empty tools are supported")
            profile["tools"] = "empty"
        if "metadata" in data:
            metadata = data["metadata"]
            if (
                not isinstance(metadata, dict)
                or set(metadata) != {"user_id"}
                or not isinstance(metadata["user_id"], str)
                or not 0 < len(metadata["user_id"].encode("utf-8")) <= _MAX_METADATA_BYTES
            ):
                raise ValueError("unsupported metadata shape")
            profile["metadata"] = {"user_id": "bounded_string"}
        for name, expected in (
            ("thinking", {"type": "adaptive", "display": "omitted"}),
            ("output_config", {"effort": "high"}),
            ("context_management", {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}),
        ):
            if name in data:
                if data[name] != expected:
                    raise ValueError("unsupported native feature shape")
                profile[name] = expected
        if "cache_control" in data:
            profile["cache_control"] = _cache(data["cache_control"])
    except (TypeError, ValueError, UnicodeError):
        reasons.add("request_feature_unsupported")
    try:
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages required")
        shapes = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message.get("role") not in ("user", "assistant")
            ):
                raise ValueError("unsupported message shape")
            shapes.append({"role": message["role"], "content": _text(message["content"])})
        profile["messages"] = shapes
        if "system" in data:
            profile["system"] = _text(data["system"])
        facts.update(input_modality="text", output_modality="text")
    except (TypeError, ValueError):
        reasons.add("input_modality_unsupported")
    return facts, reasons, profile
