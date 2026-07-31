# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the companion integration guide."""

from __future__ import annotations

import re
from pathlib import Path

from tokenpak.companion import _style
from tokenpak.companion.mcp.tools import TOOLS

GUIDE = Path(__file__).parents[2] / "tokenpak" / "companion" / "GUIDE.md"


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_guide_style_default_matches_runtime() -> None:
    content = _guide()
    assert f"| `TOKENPAK_COMPANION_STYLE` | `{_style.DEFAULT}` |" in content
    assert "set `lean` to explicitly opt into dense technical markdown" in content


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


def _retention_section() -> str:
    content = _guide()
    start = content.index("### Codex session-home retention")
    return content[start : content.index("\n### ", start + 1)]


def test_guide_documents_every_retention_cap_at_its_real_value() -> None:
    """Documented caps must track the constants the sweep actually enforces.

    The footprint table is the disclosure of what the companion writes to the
    user's machine; a doc that drifts from the code understates the footprint.
    """
    from tokenpak.companion.codex.session_home import (
        RETENTION_MAX_AGE_S,
        RETENTION_MAX_HOMES,
        RETENTION_MAX_TOTAL_BYTES,
    )

    section = _retention_section()
    assert f"| {RETENTION_MAX_HOMES} |" in section
    assert f"| {RETENTION_MAX_AGE_S // 86400} days |" in section
    assert f"| {RETENTION_MAX_TOTAL_BYTES // (1024 * 1024)} MB |" in section


def test_guide_discloses_the_unpruned_stores_and_why() -> None:
    """Stores with no sweep must be disclosed as such, not silently omitted."""
    content = _guide()
    start = content.index("## Disk footprint and retention")
    section = content[start : content.index("\n## ", start + 1)]

    for store in ("journal.db", "recall.db", "budget.db", "capsules/"):
        assert store in section, f"footprint table omits {store}"
    assert "they are memory, not cache" in section
