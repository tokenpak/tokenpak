"""
Unit tests for tokenpak.agent.proxy.tool_schema_registry
"""

import json
import threading

import pytest

from tokenpak.agent.proxy.tool_schema_registry import (
    ToolSchemaRegistry,
    get_registry,
    _normalize_tools,
    _serialize,
    _sha256,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_body(tools: list) -> bytes:
    return json.dumps({"model": "claude-3-5-sonnet", "tools": tools}).encode("utf-8")


TOOL_A = {"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object", "properties": {}}}
TOOL_B = {"name": "search_web", "description": "Search the web", "input_schema": {"type": "object", "properties": {}}}


# ---------------------------------------------------------------------------
# 1. Singleton — get_registry() returns the same object every call
# ---------------------------------------------------------------------------

class TestGetRegistrySingleton:
    def test_same_instance_on_repeated_calls(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2, "get_registry() must return the same singleton instance"

    def test_returns_tool_schema_registry_instance(self):
        assert isinstance(get_registry(), ToolSchemaRegistry)


# ---------------------------------------------------------------------------
# 2. normalize_request — body WITH tools
# ---------------------------------------------------------------------------

class TestNormalizeRequestWithTools:
    def setup_method(self):
        # Use a fresh registry for each test so frozen state is isolated
        self.reg = ToolSchemaRegistry()

    def test_returns_bytes_and_bool(self):
        body = make_body([TOOL_A])
        result = self.reg.normalize_request(body)
        assert isinstance(result, tuple) and len(result) == 2
        new_body, changed = result
        assert isinstance(new_body, bytes)
        assert isinstance(changed, bool)

    def test_tools_are_sorted_by_name(self):
        """Tools returned in the body should be sorted alphabetically by name."""
        body = make_body([TOOL_B, TOOL_A])  # B before A deliberately
        new_body, _ = self.reg.normalize_request(body)
        data = json.loads(new_body)
        names = [t["name"] for t in data["tools"]]
        assert names == sorted(names), f"Expected sorted tool names, got {names}"

    def test_idempotent_on_second_call(self):
        """Calling normalize_request twice with the same tools → identical output."""
        body = make_body([TOOL_A, TOOL_B])
        new_body1, _ = self.reg.normalize_request(body)
        new_body2, _ = self.reg.normalize_request(body)
        assert new_body1 == new_body2, "Repeated calls with same tools must be byte-identical"

    def test_changed_flag_false_on_same_tools(self):
        body = make_body([TOOL_A])
        _, changed1 = self.reg.normalize_request(body)
        _, changed2 = self.reg.normalize_request(body)
        assert changed1 is False
        assert changed2 is False

    def test_changed_flag_true_when_tools_actually_change(self):
        body1 = make_body([TOOL_A])
        body2 = make_body([TOOL_B])
        self.reg.normalize_request(body1)  # freeze with TOOL_A
        _, changed = self.reg.normalize_request(body2)  # now TOOL_B
        assert changed is True

    def test_total_requests_increments(self):
        body = make_body([TOOL_A])
        assert self.reg.total_requests == 0
        self.reg.normalize_request(body)
        self.reg.normalize_request(body)
        assert self.reg.total_requests == 2

    def test_non_tools_fields_preserved(self):
        """Other fields in the request body must be preserved unchanged."""
        body = json.dumps({"model": "claude-3", "max_tokens": 1024, "tools": [TOOL_A]}).encode()
        new_body, _ = self.reg.normalize_request(body)
        data = json.loads(new_body)
        assert data["model"] == "claude-3"
        assert data["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# 3. normalize_request — body WITHOUT tools (should not crash)
# ---------------------------------------------------------------------------

class TestNormalizeRequestNoTools:
    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_no_tools_key_returns_original(self):
        body = json.dumps({"model": "gpt-4o", "messages": []}).encode()
        new_body, changed = self.reg.normalize_request(body)
        assert new_body == body
        assert changed is False

    def test_empty_tools_list_returns_original(self):
        body = json.dumps({"tools": []}).encode()
        new_body, changed = self.reg.normalize_request(body)
        assert new_body == body
        assert changed is False

    def test_invalid_json_does_not_crash(self):
        bad_body = b"not json at all!!!"
        new_body, changed = self.reg.normalize_request(bad_body)
        assert new_body == bad_body
        assert changed is False

    def test_tools_not_list_returns_original(self):
        body = json.dumps({"tools": "should_be_a_list"}).encode()
        new_body, changed = self.reg.normalize_request(body)
        assert new_body == body
        assert changed is False


# ---------------------------------------------------------------------------
# 4. get_frozen_text / get_frozen_hash / stats
# ---------------------------------------------------------------------------

class TestPublicAccessors:
    def setup_method(self):
        self.reg = ToolSchemaRegistry()

    def test_frozen_text_none_before_first_request(self):
        assert self.reg.get_frozen_text() is None

    def test_frozen_hash_none_before_first_request(self):
        assert self.reg.get_frozen_hash() is None

    def test_frozen_text_set_after_request(self):
        self.reg.normalize_request(make_body([TOOL_A]))
        text = self.reg.get_frozen_text()
        assert text is not None
        assert isinstance(text, str)

    def test_frozen_hash_set_after_request(self):
        self.reg.normalize_request(make_body([TOOL_A]))
        h = self.reg.get_frozen_hash()
        assert h is not None
        assert len(h) == 16  # first 16 hex chars

    def test_stats_structure(self):
        self.reg.normalize_request(make_body([TOOL_A]))
        s = self.reg.stats()
        expected_keys = {
            "frozen_tools", "frozen_bytes", "frozen_tokens_approx",
            "frozen_hash", "frozen_at", "total_requests", "schema_changes", "bytes_saved",
        }
        assert expected_keys.issubset(s.keys())
        assert s["total_requests"] == 1
        assert s["frozen_tools"] == 1


# ---------------------------------------------------------------------------
# 5. Thread safety — concurrent normalize_request calls don't crash
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_requests_same_tools(self):
        reg = ToolSchemaRegistry()
        body = make_body([TOOL_A, TOOL_B])
        errors = []

        def worker():
            try:
                for _ in range(20):
                    reg.normalize_request(body)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"


# ---------------------------------------------------------------------------
# 6. Internal helpers (smoke tests)
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_normalize_tools_sorts_by_name(self):
        tools = [TOOL_B, TOOL_A]
        result = _normalize_tools(tools)
        assert result[0]["name"] == "get_weather"
        assert result[1]["name"] == "search_web"

    def test_serialize_is_deterministic(self):
        tools = _normalize_tools([TOOL_A, TOOL_B])
        s1 = _serialize(tools)
        s2 = _serialize(tools)
        assert s1 == s2

    def test_sha256_returns_64_char_hex(self):
        h = _sha256("hello world")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
