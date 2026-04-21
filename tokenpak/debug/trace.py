# SPDX-License-Identifier: Apache-2.0
"""
tokenpak.trace — Debug Trace Side-Channel
==========================================

Attaches diagnostic metadata to TokenPak responses WITHOUT leaking into
assistant message content.

Delivery options (mutually exclusive):
  1. HTTP response header: ``X-TokenPak-Trace: <base64url(json)>``
  2. JSON envelope field:  ``tokenpak_trace`` (top-level, stripped before
     forwarding to channels)

The trace is NEVER appended to ``assistant.content``.

Usage::

    from tokenpak.trace import TraceBuilder, attach_trace_header, strip_trace

    trace = (
        TraceBuilder()
        .routing(provider="anthropic", model="claude-3-haiku", reason="budget_tier")
        .budget(tier="economy", tokens=4096, reasons=["cost_optimise"])
        .retrieval(sources=["cache"], top_k=5, coverage=0.87, cache_hit=True)
        .packing(kept_turns=6, dropped_turns=2, inject_tokens=312)
        .economics(actual_tokens=1800, cost_usd=0.0012, savings_usd=0.0038)
        .build()
    )

    # Option A — HTTP header
    headers = attach_trace_header({}, trace)

    # Option B — JSON envelope
    response_dict = attach_trace_envelope(response_dict, trace)

    # Strip before channel forwarding
    headers = strip_trace_header(headers)
    response_dict = strip_trace(response_dict)
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRACE_HEADER = "X-TokenPak-Trace"
TRACE_ENVELOPE_KEY = "tokenpak_trace"
# Header name normalised to lowercase for dict comparisons
_TRACE_HEADER_LOWER = TRACE_HEADER.lower()


# ---------------------------------------------------------------------------
# Trace schema
# ---------------------------------------------------------------------------


@dataclass
class TokenPakTrace:
    """Diagnostic metadata attached to a TokenPak proxy response.

    All fields are plain-Python types so the dataclass can be serialised with
    ``dataclasses.asdict`` and round-tripped through JSON / base64url.

    Attributes
    ----------
    trace_id:
        UUIDv4 unique per request.
    timestamp:
        ISO-8601 UTC timestamp when the trace was created.
    routing:
        Routing decision: provider chosen, model, and reason string.
    budget:
        Budget tier applied, token allocation, and list of reasons.
    retrieval:
        Context-retrieval stats: sources used, top-k, coverage score,
        and whether the semantic cache was hit.
    packing:
        Wire-format packing stats: kept/dropped turns and injected tokens.
    economics:
        Token economics: actual tokens consumed, cost in USD, and savings
        (i.e. tokens/cost avoided by retrieval / caching).
    warnings:
        Free-form warning strings raised during processing (non-fatal).
    """

    trace_id: str
    timestamp: str
    routing: Dict[str, Any]  # provider, model, reason
    budget: Dict[str, Any]  # tier, tokens, reasons
    retrieval: Dict[str, Any]  # sources, top_k, coverage, cache_hit
    packing: Dict[str, Any]  # kept_turns, dropped_turns, inject_tokens
    economics: Dict[str, Any]  # actual_tokens, cost_usd, savings_usd
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation suitable for JSON encoding."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialise to a compact JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def to_base64url(self) -> str:
        """Encode to URL-safe base64 (no padding) for use in HTTP headers."""
        raw = self.to_json().encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def from_base64url(cls, encoded: str) -> "TokenPakTrace":
        """Decode a trace from a base64url-encoded header value."""
        # Re-add stripped padding
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        raw = base64.urlsafe_b64decode(encoded)
        data = json.loads(raw.decode("utf-8"))
        return cls(**data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenPakTrace":
        """Construct from a plain dict (e.g. parsed from JSON envelope)."""
        return cls(**data)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class TraceBuilder:
    """Fluent builder for :class:`TokenPakTrace`.

    Example::

        trace = (
            TraceBuilder()
            .routing("anthropic", "claude-3-haiku", "economy_tier")
            .budget("economy", 4096, ["cost_optimise"])
            .retrieval(["semantic_cache"], top_k=5, coverage=0.87, cache_hit=True)
            .packing(kept_turns=6, dropped_turns=2, inject_tokens=312)
            .economics(actual_tokens=1800, cost_usd=0.0012, savings_usd=0.0038)
            .build()
        )
    """

    def __init__(self) -> None:
        self._trace_id: str = str(uuid.uuid4())
        self._timestamp: str = datetime.now(timezone.utc).isoformat()
        self._routing: Dict[str, Any] = {}
        self._budget: Dict[str, Any] = {}
        self._retrieval: Dict[str, Any] = {}
        self._packing: Dict[str, Any] = {}
        self._economics: Dict[str, Any] = {}
        self._warnings: List[str] = []

    # ---- fluent setters --------------------------------------------------

    def routing(
        self,
        provider: str,
        model: str,
        reason: str = "",
        *,
        rule_id: Optional[str] = None,
    ) -> "TraceBuilder":
        self._routing = {
            "provider": provider,
            "model": model,
            "reason": reason,
            "rule_id": rule_id,
        }
        return self

    def budget(
        self,
        tier: str,
        tokens: int,
        reasons: Optional[List[str]] = None,
        *,
        trim_applied: bool = False,
    ) -> "TraceBuilder":
        self._budget = {
            "tier": tier,
            "tokens": tokens,
            "reasons": reasons or [],
            "trim_applied": trim_applied,
        }
        return self

    def retrieval(
        self,
        sources: Optional[List[str]] = None,
        top_k: int = 0,
        coverage: float = 0.0,
        cache_hit: bool = False,
        *,
        retrieval_ms: Optional[float] = None,
    ) -> "TraceBuilder":
        self._retrieval = {
            "sources": sources or [],
            "top_k": top_k,
            "coverage": round(coverage, 4),
            "cache_hit": cache_hit,
            "retrieval_ms": retrieval_ms,
        }
        return self

    def packing(
        self,
        kept_turns: int = 0,
        dropped_turns: int = 0,
        inject_tokens: int = 0,
        *,
        compression_ratio: Optional[float] = None,
    ) -> "TraceBuilder":
        self._packing = {
            "kept_turns": kept_turns,
            "dropped_turns": dropped_turns,
            "inject_tokens": inject_tokens,
            "compression_ratio": compression_ratio,
        }
        return self

    def economics(
        self,
        actual_tokens: int = 0,
        cost_usd: float = 0.0,
        savings_usd: float = 0.0,
        *,
        baseline_tokens: Optional[int] = None,
        baseline_cost_usd: Optional[float] = None,
    ) -> "TraceBuilder":
        """Record token economics.

        ``savings_usd`` is the cost avoided vs. a naive (no-compression)
        baseline.  If *baseline_cost_usd* is supplied and *savings_usd* is 0,
        savings are computed automatically.
        """
        if savings_usd == 0.0 and baseline_cost_usd is not None:
            savings_usd = max(0.0, baseline_cost_usd - cost_usd)
        self._economics = {
            "actual_tokens": actual_tokens,
            "cost_usd": round(cost_usd, 8),
            "savings_usd": round(savings_usd, 8),
            "baseline_tokens": baseline_tokens,
            "baseline_cost_usd": (
                round(baseline_cost_usd, 8) if baseline_cost_usd is not None else None
            ),
        }
        return self

    def warn(self, message: str) -> "TraceBuilder":
        self._warnings.append(message)
        return self

    def build(self) -> TokenPakTrace:
        """Construct and return the :class:`TokenPakTrace`."""
        return TokenPakTrace(
            trace_id=self._trace_id,
            timestamp=self._timestamp,
            routing=self._routing,
            budget=self._budget,
            retrieval=self._retrieval,
            packing=self._packing,
            economics=self._economics,
            warnings=self._warnings,
        )


# ---------------------------------------------------------------------------
# Attachment helpers — HTTP header
# ---------------------------------------------------------------------------


def attach_trace_header(
    headers: Dict[str, str],
    trace: TokenPakTrace,
) -> Dict[str, str]:
    """Return a *copy* of *headers* with the trace injected as a header.

    The trace is base64url-encoded so it is safe in HTTP headers.
    """
    result = dict(headers)
    result[TRACE_HEADER] = trace.to_base64url()
    return result


def strip_trace_header(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a *copy* of *headers* with any trace header removed.

    Both the canonical casing (``X-TokenPak-Trace``) and any lowercase
    variant are removed, so this is safe whether headers are normalised or not.
    """
    return {k: v for k, v in headers.items() if k.lower() != _TRACE_HEADER_LOWER}


def read_trace_header(headers: Dict[str, str]) -> Optional[TokenPakTrace]:
    """Parse a :class:`TokenPakTrace` from response headers, if present."""
    for k, v in headers.items():
        if k.lower() == _TRACE_HEADER_LOWER:
            try:
                return TokenPakTrace.from_base64url(v)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Attachment helpers — JSON envelope
# ---------------------------------------------------------------------------


def attach_trace_envelope(
    response: Dict[str, Any],
    trace: TokenPakTrace,
) -> Dict[str, Any]:
    """Return a *copy* of *response* with the trace injected as an envelope key.

    The key ``tokenpak_trace`` is added at the top level.  It is the
    caller's responsibility to strip the key before forwarding to channels.
    """
    result = dict(response)
    result[TRACE_ENVELOPE_KEY] = trace.to_dict()
    return result


def strip_trace(response: Dict[str, Any]) -> Dict[str, Any]:
    """Return a *copy* of *response* with the ``tokenpak_trace`` key removed.

    Safe to call even if the key is absent.  Use this before forwarding a
    response to any channel or client.
    """
    return {k: v for k, v in response.items() if k != TRACE_ENVELOPE_KEY}


def read_trace_envelope(response: Dict[str, Any]) -> Optional[TokenPakTrace]:
    """Parse a :class:`TokenPakTrace` from a response envelope, if present."""
    raw = response.get(TRACE_ENVELOPE_KEY)
    if raw is None:
        return None
    try:
        return TokenPakTrace.from_dict(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# No-leak guard
# ---------------------------------------------------------------------------


def assert_no_leak(response: Dict[str, Any]) -> None:
    """Raise :exc:`AssertionError` if trace data is present in assistant content.

    Checks the ``choices[*].message.content`` path (OpenAI-style) and the
    ``content[*].text`` path (Anthropic-style).

    This is intended for use in tests and CI pipelines.
    """
    # Reject if envelope key present at top level (should have been stripped)
    if TRACE_ENVELOPE_KEY in response:
        raise AssertionError(
            f"Trace envelope key '{TRACE_ENVELOPE_KEY}' found in response dict; "
            "strip_trace() was not called before forwarding."
        )

    def _check_content(text: str, label: str) -> None:
        if TRACE_ENVELOPE_KEY in text or TRACE_HEADER in text:
            raise AssertionError(
                f"Trace marker found in {label}; trace must not appear in assistant content."
            )

    # OpenAI-style: choices[*].message.content
    for choice in response.get("choices", []):
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        if isinstance(content, str):
            _check_content(content, "choices[].message.content")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    _check_content(block["text"], "choices[].message.content[].text")

    # Anthropic-style: content[*].text
    for block in response.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            _check_content(block["text"], "content[].text")
