# SPDX-License-Identifier: Apache-2.0
"""Content-free observations of one final request and its provider response.

These facts support a later workload quote. They are not prices, invoice
amounts, target-seed measurements, or permission to recommend or spend.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from urllib.parse import urlsplit

_MAX_BYTES = 8 * 1024 * 1024
_MAX_JSON = 4096
_MODEL = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_REASONS = frozenset(
    {
        "request_unavailable",
        "request_invalid",
        "route_unsupported",
        "headers_unsupported",
        "model_unavailable",
        "request_feature_unsupported",
        "input_modality_unsupported",
        "cache_ttl_unavailable",
        "cache_ttl_mixed",
        "response_unavailable",
        "response_invalid",
        "response_incomplete",
        "response_model_mismatch",
        "usage_unavailable",
        "service_tier_unavailable",
        "region_unavailable",
        "service_tier_mismatch",
        "region_mismatch",
        "cache_usage_mismatch",
        "response_feature_unsupported",
    }
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous JSON")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("nonfinite JSON")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON")
    return result


def _json(raw: bytes | str):
    if len(raw) > _MAX_BYTES:
        raise ValueError("observation size limit exceeded")
    return json.loads(
        raw, object_pairs_hook=_object, parse_constant=_invalid_constant, parse_float=_finite_float
    )


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _model(value):
    return value if isinstance(value, str) and _MODEL.fullmatch(value) else None


@dataclass(frozen=True)
class RequestWorkload:
    body_sha256: str | None = None
    provider: str | None = None
    model: str | None = None
    input_modality: str | None = None
    output_modality: str | None = None
    cache_ttl_seconds: int | None = None
    requested_service_tier: str | None = None
    requested_region: str | None = None
    response_model: str | None = None
    service_tier: str | None = None
    region: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    response_complete: bool = False
    reason_codes: tuple[str, ...] = ("response_unavailable",)
    schema_version: str = "native-request-workload/1"

    def __post_init__(self):
        if self.schema_version != "native-request-workload/1":
            raise ValueError("unsupported workload schema")
        if self.body_sha256 is not None and (
            not isinstance(self.body_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.body_sha256) is None
        ):
            raise ValueError("invalid body digest")
        for name, choices in (
            ("provider", {"anthropic"}),
            ("input_modality", {"text"}),
            ("output_modality", {"text"}),
            ("cache_ttl_seconds", {300, 3600}),
            ("requested_service_tier", {"auto", "standard_only"}),
            ("requested_region", {"global", "us"}),
            ("service_tier", {"standard", "priority", "batch"}),
            ("region", {"global", "us"}),
        ):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not (int if name == "cache_ttl_seconds" else str)
                or value not in choices
            ):
                raise ValueError("invalid workload selector")
        for name in ("model", "response_model"):
            value = getattr(self, name)
            if value is not None and _model(value) is None:
                raise ValueError("invalid workload model")
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, name)
            if value is not None and _count(value) is None:
                raise ValueError("invalid workload count")
        if type(self.response_complete) is not bool or type(self.reason_codes) is not tuple:
            raise ValueError("invalid workload state")
        if any(type(r) is not str or r not in _REASONS for r in self.reason_codes):
            raise ValueError("invalid workload reason")
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise ValueError("noncanonical workload reasons")
        if not self.reason_codes:
            required = (
                "body_sha256",
                "provider",
                "model",
                "input_modality",
                "output_modality",
                "cache_ttl_seconds",
                "response_model",
                "service_tier",
                "region",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
            if not self.response_complete or any(getattr(self, n) is None for n in required):
                raise ValueError("incomplete usable workload")
            if self.model != self.response_model or (
                self.cache_read_tokens + self.cache_write_tokens > self.input_tokens
            ):
                raise ValueError("contradictory usable workload")
            if self.requested_region and self.requested_region != self.region:
                raise ValueError("contradictory region")
            if self.requested_service_tier == "standard_only" and self.service_tier != "standard":
                raise ValueError("contradictory service tier")

    def to_dict(self):
        result = asdict(self)
        result["reason_codes"] = list(self.reason_codes)
        return result

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, raw: str):
        if not isinstance(raw, str) or len(raw) > _MAX_JSON:
            raise ValueError("invalid stored workload")
        data = _json(raw)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("invalid workload fields")
        if not isinstance(data["reason_codes"], list):
            raise ValueError("invalid workload reasons")
        data["reason_codes"] = tuple(data["reason_codes"])
        return cls(**data)


def observe_request(body: bytes | None, url: str | None, headers=None) -> RequestWorkload:
    """Classify the actual send; absent/unsupported inputs stay explicit."""
    reasons = {"response_unavailable"}
    if not isinstance(body, bytes) or not body or len(body) > _MAX_BYTES:
        return RequestWorkload(reason_codes=("request_unavailable", "response_unavailable"))
    facts = {"body_sha256": hashlib.sha256(body).hexdigest()}
    try:
        route = urlsplit(url or "")
        if (
            route.scheme != "https"
            or route.hostname != "api.anthropic.com"
            or route.port not in (None, 443)
            or route.username is not None
            or route.password is not None
            or route.path != "/v1/messages"
            or route.query
            or route.fragment
        ):
            reasons.add("route_unsupported")
        else:
            facts["provider"] = "anthropic"
        lowered = {}
        for name, value in (headers or {}).items():
            name = name.lower()
            if name in lowered:
                reasons.add("headers_unsupported")
            lowered[name] = value
        if (
            "anthropic-beta" in lowered
            or "authorization" in lowered
            or "x-api-key" not in lowered
            or lowered.get("content-encoding", "identity") != "identity"
        ):
            reasons.add("headers_unsupported")
        data = _json(body)
        if not isinstance(data, dict):
            raise ValueError("request is not an object")
        model = _model(data.get("model"))
        facts["model"] = model
        if model is None:
            reasons.add("model_unavailable")
        allowed = {
            "model",
            "messages",
            "max_tokens",
            "system",
            "tools",
            "stream",
            "service_tier",
            "inference_geo",
            "cache_control",
            "thinking",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "metadata",
            "output_config",
        }
        if set(data) - allowed:
            reasons.add("request_feature_unsupported")
        output_config = data.get("output_config", {})
        if not isinstance(output_config, dict) or set(output_config) - {"effort", "format"}:
            reasons.add("request_feature_unsupported")
        elif "format" in output_config:
            output_format = output_config["format"]
            if (
                not isinstance(output_format, dict)
                or set(output_format) - {"type", "schema"}
                or output_format.get("type") != "json_schema"
            ):
                reasons.add("request_feature_unsupported")
        for key, name, values in (
            ("service_tier", "requested_service_tier", ("auto", "standard_only")),
            ("inference_geo", "requested_region", ("global", "us")),
        ):
            if key in data:
                if data[key] not in values:
                    reasons.add("request_feature_unsupported")
                else:
                    facts[name] = data[key]
        ttls = set()

        def cache(value):
            if not isinstance(value, dict) or set(value) - {"type", "ttl"}:
                raise ValueError("unsupported cache directive")
            if value.get("type") != "ephemeral" or value.get("ttl", "5m") not in ("5m", "1h"):
                raise ValueError("unsupported cache lifetime")
            ttls.add(3600 if value.get("ttl") == "1h" else 300)

        def blocks(value, depth=0):
            if depth > 8:
                return False
            if isinstance(value, str):
                return True
            if not isinstance(value, list):
                return False
            for block in value:
                if not isinstance(block, dict):
                    return False
                kind = block.get("type")
                if kind not in ("text", "tool_use", "tool_result", "thinking", "redacted_thinking"):
                    return False
                if "cache_control" in block:
                    cache(block["cache_control"])
                if kind == "text" and not isinstance(block.get("text"), str):
                    return False
                if kind == "tool_result" and not blocks(block.get("content"), depth + 1):
                    return False
            return True

        messages = data.get("messages")
        text_only = (
            isinstance(messages, list)
            and bool(messages)
            and all(
                isinstance(m, dict)
                and set(m) <= {"role", "content"}
                and m.get("role") in ("user", "assistant")
                and blocks(m.get("content"))
                for m in messages
            )
        )
        if "system" in data:
            text_only = blocks(data["system"]) and text_only
        for tool in data.get("tools", []):
            if not isinstance(tool, dict) or tool.get("type", "custom") != "custom":
                reasons.add("request_feature_unsupported")
            elif "cache_control" in tool:
                cache(tool["cache_control"])
        if "cache_control" in data:
            cache(data["cache_control"])
        if text_only:
            facts.update(input_modality="text", output_modality="text")
        else:
            reasons.add("input_modality_unsupported")
        if len(ttls) == 1:
            facts["cache_ttl_seconds"] = next(iter(ttls))
        else:
            reasons.add("cache_ttl_mixed" if ttls else "cache_ttl_unavailable")
    except (TypeError, ValueError, UnicodeError, RecursionError, AttributeError):
        reasons.add("request_invalid")
    return RequestWorkload(**facts, reason_codes=tuple(sorted(reasons)))


def _response(raw: bytes, streaming: bool):
    if not streaming:
        data = _json(raw)
        return data["model"], data["usage"]
    if len(raw) > _MAX_BYTES:
        raise ValueError("response size limit")
    # Parse every data event strictly. Silently skipped malformed usage cannot
    # support completeness, even when a later terminal marker is present.
    model, usage, started, terminal, delta = None, {}, False, False, False
    for block in raw.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
        lines = [
            line[5:].removeprefix(" ") for line in block.splitlines() if line.startswith("data:")
        ]
        if not lines:
            continue
        event = _json("\n".join(lines))
        kind = event["type"]
        if terminal:
            raise ValueError("event after completion")
        if kind == "message_start":
            if started:
                raise ValueError("multiple messages")
            started = True
            model = event["message"]["model"]
            usage.update(event["message"]["usage"])
        elif kind == "message_delta":
            if not started:
                raise ValueError("delta without message")
            current = event["usage"]
            if not isinstance(current, dict) or set(current) != {"output_tokens"}:
                raise ValueError("unsupported cumulative usage update")
            count = _count(current.get("output_tokens"))
            previous = _count(usage.get("output_tokens"))
            if count is None or previous is None or count < previous:
                raise ValueError("invalid cumulative usage")
            usage.update(current)
            delta = _count(event["usage"].get("output_tokens")) is not None
        elif kind == "message_stop":
            terminal = True
        elif kind not in (
            "ping",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
        ):
            raise ValueError("unsupported response event")
    if not (started and terminal and delta):
        raise ValueError("incomplete stream")
    return model, usage


def observe_response(
    request: RequestWorkload, raw: bytes, *, streaming: bool, complete: bool, status: int
) -> RequestWorkload:
    """Attach actual response facts, retaining every request-side refusal."""
    reasons = set(request.reason_codes) - {"response_unavailable"}
    facts = {"response_complete": complete and status == 200}
    if not facts["response_complete"]:
        reasons.add("response_incomplete")
    try:
        if request.provider != "anthropic":
            raise ValueError("unsupported route")
        model, usage = _response(raw, streaming)
        facts["response_model"] = _model(model)
        if facts["response_model"] is None or model != request.model:
            reasons.add("response_model_mismatch")
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        for key, choices, reason in (
            ("service_tier", ("standard", "priority", "batch"), "service_tier_unavailable"),
            ("inference_geo", ("global", "us"), "region_unavailable"),
        ):
            value = usage.get(key)
            if value not in choices:
                reasons.add(reason)
            else:
                facts["region" if key == "inference_geo" else key] = value
        if request.requested_region and facts.get("region") not in (None, request.requested_region):
            reasons.add("region_mismatch")
        if request.requested_service_tier == "standard_only" and facts.get("service_tier") not in (
            None,
            "standard",
        ):
            reasons.add("service_tier_mismatch")
        counts = [
            _count(usage.get(k))
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        ]
        if any(c is None for c in counts):
            reasons.add("usage_unavailable")
        else:
            uncached, output, read, write = counts
            if _count(uncached + read + write) is None:
                reasons.add("usage_unavailable")
            else:
                facts.update(
                    input_tokens=uncached + read + write,
                    output_tokens=output,
                    cache_read_tokens=read,
                    cache_write_tokens=write,
                )
            creation = usage.get("cache_creation")
            if isinstance(creation, dict) and set(creation) != {
                "ephemeral_5m_input_tokens",
                "ephemeral_1h_input_tokens",
            }:
                reasons.add("response_feature_unsupported")
            if write or creation is not None:
                split = (
                    [None, None]
                    if not isinstance(creation, dict)
                    else [
                        _count(creation.get("ephemeral_5m_input_tokens")),
                        _count(creation.get("ephemeral_1h_input_tokens")),
                    ]
                )
                if (
                    None in split
                    or sum(split) != write
                    or (split[0] and request.cache_ttl_seconds != 300)
                    or (split[1] and request.cache_ttl_seconds != 3600)
                ):
                    reasons.add("cache_usage_mismatch")
        allowed_usage = {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cache_creation",
            "service_tier",
            "inference_geo",
            "server_tool_use",
            "output_tokens_details",
        }
        if set(usage) - allowed_usage:
            reasons.add("response_feature_unsupported")
        server_tools = usage.get("server_tool_use", {})
        if not isinstance(server_tools, dict) or any(_count(v) != 0 for v in server_tools.values()):
            reasons.add("response_feature_unsupported")
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError, AttributeError):
        reasons.add("response_invalid")
    return replace(request, **facts, reason_codes=tuple(sorted(reasons)))
