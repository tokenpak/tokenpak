"""CCI-02: Tool schema registry verification + telemetry surfacing tests.

AC-1: Two identical Claude Code requests (same tools, different ordering)
      → registry normalizes both to byte-identical → bytes_saved > 0.

AC-2: Two Claude Code requests with materially different tools
      → registry detects schema_changes >= 1, changed=True,
        normalized bodies differ.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak.agent.proxy.tool_schema_registry", reason="module not available in current build"
)
import json

import pytest
from tokenpak.agent.proxy.tool_schema_registry import ToolSchemaRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_cc_body(tools: list[dict], compact: bool = True) -> bytes:
    """Produce a realistic Claude Code request body with the given tools list.

    compact=True  → minimal JSON (no whitespace) — baseline format.
    compact=False → indented JSON — simulates a client that sends verbose bodies;
                    the registry always re-serialises to compact, so bytes_saved > 0.
    """
    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hello"}],
        "tools": tools,
    }
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


# Real Claude Code tool shapes (condensed)
_TOOL_A = {
    "name": "Read",
    "description": "Read a file from the filesystem",
    "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string", "description": "Absolute path"}},
        "required": ["file_path"],
    },
}
_TOOL_B = {
    "name": "Write",
    "description": "Write a file to the filesystem",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    },
}
_TOOL_C = {
    "name": "Bash",
    "description": "Run a bash command",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}
_TOOL_D = {
    "name": "Grep",
    "description": "Search file contents with regex",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["pattern"],
    },
}
_TOOL_E = {
    "name": "Glob",
    "description": "Find files by glob pattern",
    "input_schema": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    },
}

_TOOLS_5 = [_TOOL_A, _TOOL_B, _TOOL_C, _TOOL_D, _TOOL_E]
_TOOLS_5_REVERSED = list(reversed(_TOOLS_5))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestToolRegistryTelemetry:
    def test_same_tools_different_order_bytes_saved(self):
        """AC-1: Same tools in different order → byte-identical output, bytes_saved > 0.

        body1 uses compact JSON, body2 uses verbose/indented JSON (reversed tool order).
        The registry always re-serialises to compact JSON, so normalization compacts
        body2 → bytes_saved > 0.  Both normalized outputs must be byte-identical.
        """
        reg = ToolSchemaRegistry()

        body1 = _make_cc_body(_TOOLS_5, compact=True)
        body2 = _make_cc_body(_TOOLS_5_REVERSED, compact=False)  # verbose → compacted by registry

        # Pre-check: bodies differ before normalization
        assert body1 != body2, "Test precondition: input bodies must differ"

        new1, changed1 = reg.normalize_request(body1)
        new2, changed2 = reg.normalize_request(body2)

        assert new1 == new2, (
            "Normalized bodies must be byte-identical for same tools in different order"
        )
        assert reg.bytes_saved > 0, f"Expected bytes_saved > 0, got {reg.bytes_saved}"
        assert not changed2, "Second request with same tool set should not flag schema_changes"

    def test_different_tools_schema_change_detected(self):
        """AC-2: Materially different tools → schema_changes >= 1, changed=True."""
        reg = ToolSchemaRegistry()

        body1 = _make_cc_body(_TOOLS_5)

        # Second request has a completely different tool replacing one of the five
        different_tool = {
            "name": "NewTool",
            "description": "A brand new tool not in the first set",
            "input_schema": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
        }
        tools_different = [_TOOL_A, _TOOL_B, _TOOL_C, _TOOL_D, different_tool]
        body2 = _make_cc_body(tools_different)

        new1, changed1 = reg.normalize_request(body1)
        new2, changed2 = reg.normalize_request(body2)

        assert changed2 is True, "Registry must flag changed=True when tool set differs"
        assert reg.schema_changes >= 1, f"Expected schema_changes >= 1, got {reg.schema_changes}"
        assert new1 != new2, "Normalized bodies must differ when tool sets are materially different"
