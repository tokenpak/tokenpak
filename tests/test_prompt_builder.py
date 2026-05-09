"""
Unit tests for prompt_builder.py — stable/volatile prefix split.

Tests verify:
1. apply_stable_cache_control marks stable system prompts
2. apply_stable_cache_control is idempotent (no double-marking)
3. inject_with_cache_boundary places volatile content after cache boundary
4. classify_system_blocks correctly identifies volatile blocks
5. Short/haiku-type requests (no vault injection) still get cache marker
6. String system prompts are normalized to list form with cache_control
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tokenpak.proxy.prompt_builder import (
    apply_stable_cache_control,
    classify_system_blocks,
    inject_with_cache_boundary,
)


def _make_body(system=None, messages=None, model="claude-sonnet-4-6") -> bytes:
    """Build a minimal Anthropic request body."""
    body: dict = {"model": model, "max_tokens": 1024}
    if system is not None:
        body["system"] = system
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    body["messages"] = messages
    return json.dumps(body).encode("utf-8")


def _parse(body_bytes: bytes) -> dict:
    return json.loads(body_bytes)


# ---------------------------------------------------------------------------
# Test 1: apply_stable_cache_control — string system prompt gets marked
# ---------------------------------------------------------------------------
def test_string_system_gets_cache_control():
    """String system prompt should become a list with cache_control: ephemeral."""
    body = _make_body(system="You are a helpful assistant.")
    result = apply_stable_cache_control(body)
    data = _parse(result)
    system = data["system"]
    assert isinstance(system, list), "string system should be converted to list"
    assert len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}, "should have cache_control"
    print("  ✅ TEST 1: string system → list with cache_control")


# ---------------------------------------------------------------------------
# Test 2: apply_stable_cache_control — list system prompt gets marked on last block
# ---------------------------------------------------------------------------
def test_list_system_last_block_marked():
    """Multi-block system prompt: cache_control goes on the last stable block."""
    system = [
        {"type": "text", "text": "Block 1: you are a helpful assistant."},
        {"type": "text", "text": "Block 2: your job is to answer questions."},
    ]
    body = _make_body(system=system)
    result = apply_stable_cache_control(body)
    data = _parse(result)
    sys_blocks = data["system"]
    assert len(sys_blocks) == 2
    assert "cache_control" not in sys_blocks[0] or sys_blocks[0].get("cache_control") is None, \
        "first block should NOT have cache_control"
    assert sys_blocks[1]["cache_control"] == {"type": "ephemeral"}, \
        "last block should have cache_control"
    print("  ✅ TEST 2: multi-block system → last block marked")


# ---------------------------------------------------------------------------
# Test 3: apply_stable_cache_control — idempotent (no double-marking)
# ---------------------------------------------------------------------------
def test_idempotent_does_not_double_mark():
    """Running apply_stable_cache_control twice should not add extra markers."""
    body = _make_body(system="You are a helpful assistant.")
    once = apply_stable_cache_control(body)
    twice = apply_stable_cache_control(once)
    data_once = _parse(once)
    data_twice = _parse(twice)
    assert data_once["system"] == data_twice["system"], \
        "second application should be idempotent"
    markers = [b for b in data_twice["system"]
               if isinstance(b, dict) and b.get("cache_control")]
    assert len(markers) == 1, f"should have exactly 1 cache_control marker, got {len(markers)}"
    print("  ✅ TEST 3: idempotent — no double-marking")


# ---------------------------------------------------------------------------
# Test 4: inject_with_cache_boundary — volatile appended after cache marker
# ---------------------------------------------------------------------------
def test_inject_places_volatile_after_cache_boundary():
    """inject_with_cache_boundary should add volatile text as last block, no cache_control."""
    body = _make_body(system="Static system prompt.")
    volatile = "## Context (retrieved)\nSome dynamic vault content here."
    result = inject_with_cache_boundary(body, volatile)
    data = _parse(result)
    sys_blocks = data["system"]
    assert len(sys_blocks) == 2, f"expected 2 blocks (stable + volatile), got {len(sys_blocks)}"
    # First block: static, should have cache_control
    assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}, \
        "static block should have cache_control"
    # Second block: volatile, should NOT have cache_control
    assert "cache_control" not in sys_blocks[1], \
        "volatile block should NOT have cache_control"
    assert volatile in sys_blocks[1]["text"], "volatile text should be in last block"
    print("  ✅ TEST 4: inject places volatile after cache boundary")


# ---------------------------------------------------------------------------
# Test 5: classify_system_blocks — timestamp/dynamic content → volatile
# ---------------------------------------------------------------------------
def test_classify_detects_volatile_blocks():
    """Blocks with timestamps or vault markers should be classified as volatile."""
    stable_block = {"type": "text", "text": "You are a helpful assistant with access to tools."}
    volatile_block_ts = {"type": "text", "text": "Current time: 2026-03-06T07:00:00Z"}
    volatile_block_ctx = {"type": "text", "text": "--- [vault/notes.md] (relevance: 3.5) ---\nSome content"}
    volatile_block_ret = {"type": "text", "text": "<retrieved_context>Some dynamic data</retrieved_context>"}

    stable, volatile = classify_system_blocks([
        stable_block, volatile_block_ts, volatile_block_ctx, volatile_block_ret
    ])
    assert len(stable) == 1, f"expected 1 stable block, got {len(stable)}"
    assert len(volatile) == 3, f"expected 3 volatile blocks, got {len(volatile)}"
    assert stable[0]["text"] == stable_block["text"]
    print("  ✅ TEST 5: classify_system_blocks detects volatile content")


# ---------------------------------------------------------------------------
# Test 6: No system prompt — apply_stable_cache_control returns body unchanged
# ---------------------------------------------------------------------------
def test_no_system_prompt_returns_unchanged():
    """Requests with no system prompt should not gain a system key.

    Note: messages may still receive cache_control markers (midpoint breakpoints)
    even when there is no system prompt — this is expected behavior.
    """
    body = _make_body(system=None)
    result = apply_stable_cache_control(body)
    data_in = _parse(body)
    data_out = _parse(result)
    assert "system" not in data_out or not data_out.get("system"), \
        "no system prompt → body should not gain a system key"
    # Messages may be annotated with cache_control breakpoints (expected behavior)
    # Verify no messages were dropped or reordered
    assert len(data_in["messages"]) == len(data_out["messages"]), \
        "message count should be unchanged"
    assert [m["role"] for m in data_in["messages"]] == [m["role"] for m in data_out["messages"]], \
        "message roles should be unchanged"
    print("  ✅ TEST 6: no system prompt → system key absent, message structure preserved")


if __name__ == "__main__":
    tests = [
        test_string_system_gets_cache_control,
        test_list_system_last_block_marked,
        test_idempotent_does_not_double_mark,
        test_inject_places_volatile_after_cache_boundary,
        test_classify_detects_volatile_blocks,
        test_no_system_prompt_returns_unchanged,
    ]
    passed = 0
    failed = 0
    print(f"\nRunning {len(tests)} unit tests for prompt_builder.py...\n")
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: unexpected error: {e}")
            import traceback; traceback.print_exc()  # noqa: I001
            failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("All tests PASSED ✅")
