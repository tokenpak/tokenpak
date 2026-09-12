# SPDX-License-Identifier: Apache-2.0
"""Completed native token facts, independent of price or subscription billing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Mapping

from .request_workload import _count, _json, _model, _response
from .request_workload import observe_request as observe_workload_request

_MAX_RECEIPT = 4096
_REASONS = frozenset(
    {
        "request_unavailable",
        "request_invalid",
        "route_unsupported",
        "headers_unsupported",
        "model_unavailable",
        "input_modality_unsupported",
        "request_feature_unsupported",
        "response_unavailable",
        "response_incomplete",
        "response_invalid",
        "response_model_mismatch",
        "usage_unavailable",
        "cache_usage_mismatch",
        "response_feature_unsupported",
    }
)
_COUNTS = (
    "uncached_input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "input_tokens",
    "output_tokens",
)


@dataclass(frozen=True)
class RequestTokenObservation:
    """Strict private metadata; no credential, body, invoice or comparison claim."""

    body_sha256: str | None = None
    provider: str | None = None
    transport: str | None = None
    authentication_kind: str | None = None
    authentication_provenance: str | None = None
    request_feature_profile_sha256: str | None = None
    feature_profile_status: str = "unclassified"
    request_model: str | None = None
    response_model: str | None = None
    input_modality: str | None = None
    output_modality: str | None = None
    uncached_input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_complete: bool = False
    token_usage_complete: bool = False
    token_reason_codes: tuple[str, ...] = ("response_unavailable",)
    comparison_reason_codes: tuple[str, ...] = ("feature_profile_unclassified",)
    billing_semantics: str = "unestablished"
    actual_cost_usd: None = None
    schema_version: str = "native-request-token-observation/1"

    def __post_init__(self) -> None:
        if self.schema_version != "native-request-token-observation/1":
            raise ValueError("unsupported token observation schema")
        for name in ("body_sha256", "request_feature_profile_sha256"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise ValueError("invalid token observation digest")
        for name, choices in (
            ("provider", {"anthropic"}),
            ("transport", {"anthropic_messages_https"}),
            ("authentication_kind", {"api_key", "oauth_bearer", "forwarded_bearer_unclassified"}),
            (
                "authentication_provenance",
                {"credential_router_metadata", "final_forwarded_headers"},
            ),
            ("input_modality", {"text"}),
            ("output_modality", {"text"}),
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or value not in choices):
                raise ValueError("invalid token observation classification")
        # No native client feature profile has yet received comparison acceptance.
        if self.feature_profile_status != "unclassified" or self.comparison_reason_codes != (
            "feature_profile_unclassified",
        ):
            raise ValueError("unsupported token comparison profile")
        if self.billing_semantics != "unestablished" or self.actual_cost_usd is not None:
            raise ValueError("token facts do not establish billing")
        if (
            self.authentication_kind == "oauth_bearer"
            and self.authentication_provenance != "credential_router_metadata"
        ):
            raise ValueError("OAuth ownership requires router metadata")
        if (
            self.authentication_kind == "forwarded_bearer_unclassified"
            and self.authentication_provenance != "final_forwarded_headers"
        ):
            raise ValueError("forwarded bearer provenance differs")
        for name in ("request_model", "response_model"):
            value = getattr(self, name)
            if value is not None and _model(value) is None:
                raise ValueError("invalid token observation model")
        for name in _COUNTS:
            value = getattr(self, name)
            if value is not None and _count(value) is None:
                raise ValueError("invalid observed token count")
        if type(self.response_complete) is not bool or type(self.token_usage_complete) is not bool:
            raise ValueError("invalid token completeness type")
        reasons = self.token_reason_codes
        if type(reasons) is not tuple or any(
            type(r) is not str or r not in _REASONS for r in reasons
        ):
            raise ValueError("invalid token observation reasons")
        if tuple(sorted(set(reasons))) != reasons or self.token_usage_complete != (not reasons):
            raise ValueError("token completeness contradicts reasons")
        if all(getattr(self, name) is not None for name in _COUNTS):
            if (
                self.input_tokens
                != self.uncached_input_tokens + self.cache_read_tokens + self.cache_creation_tokens
            ):
                raise ValueError("observed token categories do not reconcile")
        if self.token_usage_complete:
            required = (
                "body_sha256",
                "provider",
                "transport",
                "authentication_kind",
                "authentication_provenance",
                "request_feature_profile_sha256",
                "request_model",
                "response_model",
                "input_modality",
                "output_modality",
                *_COUNTS,
            )
            if not self.response_complete or any(getattr(self, name) is None for name in required):
                raise ValueError("complete token observation lacks required facts")
            if self.request_model != self.response_model:
                raise ValueError("observed request and response models differ")

    def to_dict(self) -> dict:
        result = asdict(self)
        for name in ("token_reason_codes", "comparison_reason_codes"):
            result[name] = list(result[name])
        return result

    def to_json(self) -> str:
        result = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(result.encode()) > _MAX_RECEIPT:
            raise ValueError("token observation exceeds size limit")
        return result

    @classmethod
    def from_json(cls, raw: str) -> RequestTokenObservation:
        if not isinstance(raw, str) or len(raw.encode()) > _MAX_RECEIPT:
            raise ValueError("invalid stored token observation size")
        data = _json(raw)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("invalid stored token observation fields")
        for name in ("token_reason_codes", "comparison_reason_codes"):
            if not isinstance(data[name], list):
                raise ValueError("invalid stored token observation reasons")
            data[name] = tuple(data[name])
        return cls(**data)


def observe_request(
    body: bytes | None,
    url: str | None,
    headers: Mapping[str, str] | None = None,
    *,
    credential_kind: str | None = None,
) -> RequestTokenObservation:
    """Classify final forwarding; router kind is internal metadata, never a header."""
    workload = observe_workload_request(body, url, headers)
    reasons = set(workload.reason_codes) & _REASONS
    facts = dict(
        body_sha256=workload.body_sha256,
        provider=workload.provider,
        request_model=workload.model,
        input_modality=workload.input_modality,
        output_modality=workload.output_modality,
    )
    if workload.provider == "anthropic":
        facts["transport"] = "anthropic_messages_https"
    try:
        lowered = {}
        for key, value in (headers or {}).items():
            if not isinstance(key, str) or not isinstance(value, str) or key.lower() in lowered:
                raise ValueError("ambiguous headers")
            lowered[key.lower()] = value
        if lowered.get("content-encoding", "identity") != "identity":
            raise ValueError("unsupported encoding")
        key = lowered.get("x-api-key", "")
        bearer = lowered.get("authorization", "")
        if bool(key) == bool(bearer):
            raise ValueError("one authentication scheme required")
        if bearer and (re.fullmatch(r"Bearer [\x21-\x7e]{1,8192}", bearer, re.IGNORECASE) is None):
            raise ValueError("unsupported bearer shape")
        if key and (len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise ValueError("unsupported key shape")
        if credential_kind not in (None, "oauth", "api_key"):
            raise ValueError("unsupported router kind")
        if credential_kind == "oauth" and not bearer or credential_kind == "api_key" and not key:
            raise ValueError("router metadata contradicts final authentication")
        facts["authentication_kind"] = (
            "api_key"
            if key
            else "oauth_bearer"
            if credential_kind == "oauth"
            else "forwarded_bearer_unclassified"
        )
        facts["authentication_provenance"] = (
            "credential_router_metadata"
            if credential_kind is not None
            else "final_forwarded_headers"
        )
        beta = lowered.get("anthropic-beta", "")
        if len(beta) > 2048 or any(ord(c) < 32 or ord(c) > 126 for c in beta):
            raise ValueError("unbounded feature header")
        # Only hashes and fixed classifications survive. No authentication input
        # enters this digest and no beta profile earns comparison eligibility.
        profile = dict(
            beta_sha256=hashlib.sha256(beta.encode()).hexdigest(),
            input_modality=workload.input_modality,
            output_modality=workload.output_modality,
            cache_ttl_seconds=workload.cache_ttl_seconds,
        )
        facts["request_feature_profile_sha256"] = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        reasons.discard("headers_unsupported")
    except (TypeError, ValueError, AttributeError):
        reasons.add("headers_unsupported")
    return RequestTokenObservation(**facts, token_reason_codes=tuple(sorted(reasons)))


def observe_response(
    request: RequestTokenObservation,
    raw: bytes,
    *,
    streaming: bool,
    complete: bool,
    status: int,
) -> RequestTokenObservation:
    """Attach complete native token categories without inventing price selectors."""
    reasons = set(request.token_reason_codes) - {"response_unavailable"}
    facts = {"response_complete": complete is True and type(status) is int and status == 200}
    if not facts["response_complete"]:
        reasons.add("response_incomplete")
    try:
        if request.provider != "anthropic" or type(streaming) is not bool:
            raise ValueError("unsupported response transport")
        model, usage = _response(raw, streaming)
        facts["response_model"] = _model(model)
        if model != request.request_model or facts["response_model"] is None:
            reasons.add("response_model_mismatch")
        if not isinstance(usage, dict):
            raise ValueError("invalid native usage")
        counts = [
            _count(usage.get(name))
            for name in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
            )
        ]
        if (
            any(value is None for value in counts)
            or _count(sum(value or 0 for value in counts[:3])) is None
        ):
            reasons.add("usage_unavailable")
        else:
            uncached, read, write, output = counts
            facts.update(
                uncached_input_tokens=uncached,
                cache_read_tokens=read,
                cache_creation_tokens=write,
                input_tokens=uncached + read + write,
                output_tokens=output,
            )
            split = usage.get("cache_creation")
            # The aggregate creation count is an observed token category.
            # A missing TTL partition affects pricing, not that scalar count;
            # a supplied partition must still reconcile exactly.
            if "cache_creation" in usage:
                names = {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"}
                if (
                    not isinstance(split, dict)
                    or set(split) != names
                    or any(_count(value) is None for value in split.values())
                    or sum(split.values()) != write
                ):
                    reasons.add("cache_usage_mismatch")
        if set(usage) - {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cache_creation",
            "service_tier",
            "inference_geo",
            "server_tool_use",
            "output_tokens_details",
        }:
            reasons.add("response_feature_unsupported")
        server_tools = usage.get("server_tool_use", {})
        if not isinstance(server_tools, dict) or any(
            _count(value) != 0 for value in server_tools.values()
        ):
            reasons.add("response_feature_unsupported")
        details = usage.get("output_tokens_details")
        if details is not None and (
            not isinstance(details, dict)
            or set(details) != {"reasoning_tokens"}
            or _count(details.get("reasoning_tokens")) is None
            or facts.get("output_tokens") is None
            or details["reasoning_tokens"] > facts["output_tokens"]
        ):
            reasons.add("response_feature_unsupported")
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError, AttributeError):
        reasons.add("response_invalid")
        facts["response_complete"] = False
    return replace(
        request,
        **facts,
        token_usage_complete=not reasons,
        token_reason_codes=tuple(sorted(reasons)),
    )
