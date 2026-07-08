# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the companion integration guide."""

from __future__ import annotations

import re
from pathlib import Path

from tokenpak.companion.mcp.tools import TOOLS

GUIDE = Path(__file__).parents[2] / "tokenpak" / "companion" / "GUIDE.md"


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _mcp_tools_section() -> str:
    content = _guide()
    start = content.index("## MCP Tools Reference")
    end = content.index("\n---", start)
    return content[start:end]


def _tool_table_names() -> list[str]:
    section = _mcp_tools_section()
    return re.findall(r"^\| `([A-Za-z0-9_]+)` \|", section, flags=re.MULTILINE)


def test_guide_tool_rows_match_mcp_registry() -> None:
    assert _tool_table_names() == [tool.name for tool in TOOLS]


def test_guide_documents_vault_tool_bm25_boundary() -> None:
    section = _mcp_tools_section()
    normalized = " ".join(section.split())
    assert "`vault_search`" in section
    assert "`vault_retrieve`" in section
    assert "BM25 search/retrieval over indexed vault blocks" in normalized
    assert "not structured Pak or MultiPak recall" in normalized


def test_guide_documents_vault_tool_shapes() -> None:
    section = _mcp_tools_section()
    expected = (
        '"query"',
        '"limit"',
        '"block_id"',
        '"path"',
        "`content`",
        "`path`",
        "`source_path`",
        "`tokens`",
        "`resolution`",
    )
    for text in expected:
        assert text in section
