import json

from tokenpak.agent.proxy.prompt_builder import (
    apply_deterministic_cache_breakpoints,
    get_stats,
)
from tokenpak.proxy.adapters.anthropic_adapter import AnthropicAdapter


def _mk_body() -> bytes:
    body = {
        "model": "claude-sonnet-4-6",
        "system": [
            {"type": "text", "text": "Stable system instructions."},
            {"type": "text", "text": "Policy block."},
        ],
        "tools": [
            {"name": "tool_a", "input_schema": {"type": "object"}},
            {"name": "tool_b", "input_schema": {"type": "object"}},
        ],
        "messages": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ],
    }
    return json.dumps(body).encode("utf-8")


def _count_ephemeral(data: dict) -> int:
    n = 0
    for blk in data.get("system", []):
        if isinstance(blk, dict) and blk.get("cache_control") == {"type": "ephemeral"}:
            n += 1
    for tool in data.get("tools", []):
        if isinstance(tool, dict) and tool.get("cache_control") == {"type": "ephemeral"}:
            n += 1
    for msg in data.get("messages", []):
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("cache_control") == {"type": "ephemeral"}:
                    n += 1
    return n


def test_breakpoints_deterministic_across_repeated_calls():
    b1 = apply_deterministic_cache_breakpoints(_mk_body())
    b2 = apply_deterministic_cache_breakpoints(_mk_body())
    assert b1 == b2

    d = json.loads(b1)
    # system + tools + midpoint + second-to-last assistant
    assert _count_ephemeral(d) >= 4


def test_anthropic_adapter_roundtrip_compatible_after_breakpoints():
    adapter = AnthropicAdapter()
    out = apply_deterministic_cache_breakpoints(_mk_body())
    canonical = adapter.normalize(out)
    denorm = adapter.denormalize(canonical)
    reparsed = json.loads(denorm)

    assert "messages" in reparsed
    assert isinstance(reparsed["messages"], list)
    assert reparsed["model"] == "claude-sonnet-4-6"


def test_breakpoint_telemetry_exposed():
    _ = apply_deterministic_cache_breakpoints(_mk_body())
    summary = get_stats().summary()
    bp = summary.get("breakpoint_activity", {})
    assert "applied" in bp
    assert "skipped" in bp
    assert "system_last" in bp["applied"]
    assert "tools_last" in bp["applied"]
