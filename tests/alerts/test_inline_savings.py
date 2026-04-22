"""InlineSavingsEvent — ε acceptance."""

from __future__ import annotations

import json

from tokenpak.alerts.inline_savings import (
    InlineSavingsEvent,
    build_event,
    format_oneline,
)


def test_build_event_happy_path():
    row = {
        "model": "claude-haiku-4-5",
        "input_tokens": 1000,
        "sent_input_tokens": 700,
        "output_tokens": 200,
        "cache_read_tokens": 500,
        "estimated_cost": 0.05,
        "cost_saved": 0.01,
        "cache_origin": "proxy",
        "latency_ms": 120,
    }
    ev = build_event(row, route_class="anthropic-sdk")
    assert ev.route_class == "anthropic-sdk"
    assert ev.input_tokens == 1000
    assert ev.output_tokens == 200
    assert ev.saved_tokens == 300  # 1000 - 700
    assert ev.compression_pct == 30.0
    assert ev.cache_origin == "proxy"


def test_build_event_handles_missing_fields():
    ev = build_event({})
    assert ev.input_tokens == 0
    assert ev.saved_tokens == 0
    assert ev.route_class == "generic"


def test_build_event_route_class_falls_through_to_row():
    ev = build_event({"route_class": "claude-code-tui"})
    assert ev.route_class == "claude-code-tui"


def test_as_dict_json_serialisable():
    ev = build_event({"input_tokens": 100, "model": "claude-haiku"})
    payload = json.dumps(ev.as_dict())
    assert '"input_tokens"' in payload


def test_format_oneline_claude_code():
    ev = InlineSavingsEvent(
        route_class="claude-code-tui",
        model="claude-haiku",
        input_tokens=1500,
        output_tokens=200,
        cache_read_tokens=1000,
        saved_tokens=0,
        cost_usd=0.012,
        cost_saved_usd=0.0,
        cache_origin="client",
        latency_ms=80,
    )
    line = format_oneline(ev)
    assert line.startswith("tokenpak:")
    assert "1,500" in line
    assert "cache-client" in line
    assert "$0.0120" in line


def test_format_oneline_proxy_compression():
    ev = InlineSavingsEvent(
        route_class="anthropic-sdk",
        model="claude-haiku",
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=0,
        saved_tokens=300,
        cost_usd=0.05,
        cost_saved_usd=0.015,
        cache_origin="unknown",
        latency_ms=120,
    )
    line = format_oneline(ev)
    assert "saved 300" in line
    assert "30.0%" in line


def test_no_error_rate_when_zero_input():
    ev = InlineSavingsEvent(
        route_class="generic",
        model="x",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        saved_tokens=0,
        cost_usd=0,
        cost_saved_usd=0,
        cache_origin="unknown",
        latency_ms=0,
    )
    assert ev.compression_pct == 0.0
