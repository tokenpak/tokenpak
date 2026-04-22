"""Inline-savings events — small, fast, renderable.

The proxy ships per-request savings events; various consumers render
them in-session:

- Companion TUI renders them via the stderr status line the hook
  already writes under each prompt.
- IDE extensions subscribe to SSE events at ``/v1/events/savings``
  (wiring lands as the proxy pipeline rewrite lets us, not here).
- CLI watchers (``tokenpak watch``) can pretty-print them.

This module provides:

- :class:`InlineSavingsEvent` — the immutable event shape.
- :func:`build_event` — constructs an event from a request row-like
  mapping (the same data monitor.db stores).
- :func:`format_oneline` — compact renderer that fits on the TUI
  status line.

No SSE server here — that's an API/transport concern and lands in
``proxy/stats_api.py`` when the wire layer catches up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(slots=True, frozen=True)
class InlineSavingsEvent:
    """Per-request savings summary safe to render in any channel."""

    route_class: str  # "claude-code-tui", "anthropic-sdk", ...
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    saved_tokens: int
    cost_usd: float
    cost_saved_usd: float
    cache_origin: str  # "client" | "proxy" | "unknown"
    latency_ms: int

    @property
    def compression_pct(self) -> float:
        """Percent of input tokens saved by proxy-side compression."""
        if self.input_tokens <= 0:
            return 0.0
        return round(self.saved_tokens / self.input_tokens * 100, 1)

    def as_dict(self) -> dict:
        return {
            "route_class": self.route_class,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "saved_tokens": self.saved_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cost_saved_usd": round(self.cost_saved_usd, 6),
            "cache_origin": self.cache_origin,
            "latency_ms": self.latency_ms,
            "compression_pct": self.compression_pct,
        }


def build_event(
    row: Mapping[str, object], route_class: Optional[str] = None
) -> InlineSavingsEvent:
    """Construct an event from a monitor.db-like row.

    ``route_class`` overrides the row's value when the caller has a
    more authoritative classification (e.g. the classifier just ran).
    """
    def _int(key: str) -> int:
        v = row.get(key)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    def _float(key: str) -> float:
        v = row.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    input_tokens = _int("input_tokens")
    # If the proxy actually compressed, saved_tokens = input - sent;
    # monitor.db stores the compressed count under `compressed_tokens`.
    saved = 0
    sent = _int("sent_input_tokens")
    if sent and input_tokens > sent:
        saved = input_tokens - sent

    return InlineSavingsEvent(
        route_class=str(route_class or row.get("route_class") or "generic"),
        model=str(row.get("model") or ""),
        input_tokens=input_tokens,
        output_tokens=_int("output_tokens"),
        cache_read_tokens=_int("cache_read_tokens"),
        saved_tokens=saved,
        cost_usd=_float("estimated_cost"),
        cost_saved_usd=_float("cost_saved"),
        cache_origin=str(row.get("cache_origin") or "unknown"),
        latency_ms=_int("latency_ms"),
    )


def format_oneline(ev: InlineSavingsEvent) -> str:
    """Status-line renderer: fits under Claude Code's TUI prompt."""
    parts: list[str] = []
    parts.append(f"in {ev.input_tokens:,}")
    parts.append(f"out {ev.output_tokens:,}")
    if ev.cache_read_tokens > 0:
        parts.append(
            f"cache-{ev.cache_origin} {ev.cache_read_tokens:,}"
        )
    if ev.saved_tokens > 0:
        parts.append(f"saved {ev.saved_tokens:,} ({ev.compression_pct}%)")
    if ev.cost_usd > 0:
        parts.append(f"${ev.cost_usd:.4f}")
    return "tokenpak: " + "  ".join(parts)


__all__ = ["InlineSavingsEvent", "build_event", "format_oneline"]
