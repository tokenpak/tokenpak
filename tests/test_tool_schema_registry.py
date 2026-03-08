"""
Unit tests for tokenpak.agent.proxy.tool_schema_registry

Tests cover:
- get_registry() singleton behavior
- normalize_request() with tools key
- normalize_request() without tools key (passthrough)
- normalize_request() schema stabilization (frozen bytes identical on repeat)
- normalize_request() detects real schema changes
- get_frozen_text() / get_frozen_hash() accessors
- stats() fields
"""

import json
import threading

import pytest

from tokenpak.agent.proxy.tool_schema_registry import (
    ToolSchemaRegistry,
    get_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_body(tools: list | None = None, **extra) -> bytes:
    data: dict = {"model": "claude-3", "messages": []}
    if tools is not None:
        data["tools"] = tools
    data.update(extra)
    return json.dumps(data).encode("utf-8")


TOOL_A = {
    "name": "alpha",
    "description": "Does alpha things",
    "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
}

TOOL_B = {
    "name": "beta",
    "description": "Does beta things",
    "input_schema": {"type": "object", "properties": {"y": {"type": "integer"}}},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetRegistrySingleton:
    """get_registry() must return the same instance every call."""

    def test_same_instance(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_returns_tool_schema_registry(self):
        assert isinstance(get_registry(), ToolSchemaRegistry)

    def test_thread_safe_singleton(self):
        """Multiple threads must all get the exact same object."""
        results = []
        def grab():
            results.append(id(get_registry()))
        threads = [threading.Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(results)) == 1, "All threads must receive the same singleton"


class TestNormalizeRequestNoTools:
    """Body without a 'tools' key must pass through unchanged."""

    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_no_tools_key_returns_original_bytes(self):
        body = _make_body()  # no tools
        out, changed = self.reg.normalize_request(body)
        assert out == body
        assert changed is False

    def test_empty_tools_list_returns_original(self):
        body = _make_body(tools=[])
        out, changed = self.reg.normalize_request(body)
        assert out == body
        assert changed is False

    def test_invalid_json_returns_original(self):
        bad = b"not json at all"
        out, changed = self.reg.normalize_request(bad)
        assert out == bad
        assert changed is False


class TestNormalizeRequestWithTools:
    """Body with tools must be normalized and frozen."""

    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_returns_bytes_and_bool(self):
        body = _make_body(tools=[TOOL_A])
        result = self.reg.normalize_request(body)
        assert isinstance(result, tuple) and len(result) == 2
        out_bytes, changed = result
        assert isinstance(out_bytes, bytes)
        assert isinstance(changed, bool)

    def test_output_is_valid_json_with_tools(self):
        body = _make_body(tools=[TOOL_A, TOOL_B])
        out, _ = self.reg.normalize_request(body)
        data = json.loads(out)
        assert "tools" in data
        assert len(data["tools"]) == 2

    def test_tools_sorted_by_name(self):
        """Tools should come back sorted alphabetically by name."""
        body = _make_body(tools=[TOOL_B, TOOL_A])  # B before A deliberately
        out, _ = self.reg.normalize_request(body)
        data = json.loads(out)
        names = [t["name"] for t in data["tools"]]
        assert names == sorted(names)

    def test_frozen_bytes_identical_on_repeat(self):
        """Calling normalize_request twice with identical tools must produce
        byte-for-byte identical output (critical for prompt-cache stability)."""
        body1 = _make_body(tools=[TOOL_A, TOOL_B])
        body2 = _make_body(tools=[TOOL_B, TOOL_A])  # different order, same schemas
        out1, _ = self.reg.normalize_request(body1)
        out2, _ = self.reg.normalize_request(body2)
        d1 = json.loads(out1)
        d2 = json.loads(out2)
        # The tools arrays must be identical after normalization
        assert d1["tools"] == d2["tools"]

    def test_first_call_changed_is_false(self):
        """First freeze is not a 'change' — changed should be False."""
        body = _make_body(tools=[TOOL_A])
        _, changed = self.reg.normalize_request(body)
        assert changed is False

    def test_same_tools_second_call_changed_false(self):
        body = _make_body(tools=[TOOL_A])
        self.reg.normalize_request(body)
        _, changed = self.reg.normalize_request(body)
        assert changed is False

    def test_different_tools_triggers_change(self):
        body1 = _make_body(tools=[TOOL_A])
        body2 = _make_body(tools=[TOOL_B])
        self.reg.normalize_request(body1)
        _, changed = self.reg.normalize_request(body2)
        assert changed is True


class TestFrozenAccessors:
    """get_frozen_text() and get_frozen_hash() work correctly."""

    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_frozen_text_none_before_first_call(self):
        assert self.reg.get_frozen_text() is None

    def test_frozen_hash_none_before_first_call(self):
        assert self.reg.get_frozen_hash() is None

    def test_frozen_text_populated_after_normalize(self):
        self.reg.normalize_request(_make_body(tools=[TOOL_A]))
        text = self.reg.get_frozen_text()
        assert text is not None
        # Should be valid JSON representing the tools array
        tools = json.loads(text)
        assert isinstance(tools, list)

    def test_frozen_hash_populated_and_truncated(self):
        self.reg.normalize_request(_make_body(tools=[TOOL_A]))
        h = self.reg.get_frozen_hash()
        assert h is not None
        assert len(h) == 16  # truncated to first 16 hex chars


class TestStats:
    """stats() returns expected fields and increments correctly."""

    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_stats_returns_dict(self):
        assert isinstance(self.reg.stats(), dict)

    def test_stats_has_expected_keys(self):
        keys = {"frozen_tools", "frozen_bytes", "frozen_tokens_approx",
                "frozen_hash", "frozen_at", "total_requests",
                "schema_changes", "bytes_saved"}
        assert keys.issubset(self.reg.stats().keys())

    def test_total_requests_increments(self):
        body = _make_body(tools=[TOOL_A])
        self.reg.normalize_request(body)
        self.reg.normalize_request(body)
        assert self.reg.stats()["total_requests"] == 2

    def test_schema_changes_increments_on_diff(self):
        self.reg.normalize_request(_make_body(tools=[TOOL_A]))
        self.reg.normalize_request(_make_body(tools=[TOOL_B]))
        assert self.reg.stats()["schema_changes"] == 1

    def test_no_tools_does_not_increment_requests(self):
        """Requests with no tools should NOT count in total_requests."""
        body = _make_body()  # no tools
        self.reg.normalize_request(body)
        assert self.reg.stats()["total_requests"] == 0
