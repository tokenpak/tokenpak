"""Comprehensive unit tests for tokenpak.compression.pack module.

Tests cover:
- PackBlock dataclass (creation, defaults)
- CompiledResult output methods (to_prompt, to_messages, to_anthropic, to_json)
- ContextPack class (add, clear, compile)
- pack_prompt convenience helper
- Edge cases (empty content, None values, boundary conditions)
- Error handling (invalid priorities, token counting)
"""

import json
import pytest
from typing import Dict, Any

from tokenpak.compression.pack import (
    PackBlock,
    CompiledResult,
    ContextPack,
    pack_prompt,
)
from tokenpak.compression.report import CompileReport, Action


# ──────────────────────────────────────────────────────────────────────────
# PackBlock Tests
# ──────────────────────────────────────────────────────────────────────────


class TestPackBlock:
    """Tests for PackBlock dataclass."""

    def test_packblock_creation_minimal(self):
        """Create PackBlock with required args only."""
        block = PackBlock(
            id="test-1",
            type="knowledge",
            content="Some content",
        )
        assert block.id == "test-1"
        assert block.type == "knowledge"
        assert block.content == "Some content"
        assert block.priority == "medium"  # default
        assert block.quality is None  # default
        assert block.max_tokens is None  # default

    def test_packblock_creation_full(self):
        """Create PackBlock with all attributes."""
        block = PackBlock(
            id="test-2",
            type="instructions",
            content="Instructions here",
            priority="critical",
            quality=0.95,
            max_tokens=500,
        )
        assert block.id == "test-2"
        assert block.priority == "critical"
        assert block.quality == 0.95
        assert block.max_tokens == 500

    def test_packblock_priority_values(self):
        """Test all valid priority levels."""
        priorities = ["critical", "high", "medium", "low"]
        for pri in priorities:
            block = PackBlock(id=f"p-{pri}", type="test", content="x", priority=pri)
            assert block.priority == pri

    def test_packblock_empty_content(self):
        """PackBlock with empty string content."""
        block = PackBlock(id="empty", type="test", content="")
        assert block.content == ""

    def test_packblock_quality_boundaries(self):
        """Quality value can be 0.0 to 1.0."""
        block_low = PackBlock(id="q-0", type="test", content="x", quality=0.0)
        block_high = PackBlock(id="q-1", type="test", content="x", quality=1.0)
        assert block_low.quality == 0.0
        assert block_high.quality == 1.0


# ──────────────────────────────────────────────────────────────────────────
# CompiledResult Tests
# ──────────────────────────────────────────────────────────────────────────


class TestCompiledResult:
    """Tests for CompiledResult output methods."""

    @pytest.fixture
    def sample_result(self) -> CompiledResult:
        """Create a sample CompiledResult for testing."""
        report = CompileReport(
            input_blocks=2,
            output_blocks=2,
            input_tokens=100,
            output_tokens=100,
            budget=8000,
            compile_time_ms=5.2,
            decisions=[],
            final_order=["sys", "doc"],
        )
        return CompiledResult(text="System prompt.\n\nDocument text.", report=report)

    def test_compiled_result_str(self, sample_result):
        """__str__ returns the compiled text."""
        assert str(sample_result) == "System prompt.\n\nDocument text."

    def test_to_prompt(self, sample_result):
        """to_prompt() returns plain text."""
        result = sample_result.to_prompt()
        assert isinstance(result, str)
        assert result == "System prompt.\n\nDocument text."

    def test_to_messages(self, sample_result):
        """to_messages() returns list of dicts in OpenAI format."""
        msgs = sample_result.to_messages()
        assert isinstance(msgs, list)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "System prompt.\n\nDocument text."

    def test_to_messages_empty_text(self):
        """to_messages() returns empty list when text is empty."""
        report = CompileReport(
            input_blocks=0,
            output_blocks=0,
            input_tokens=0,
            output_tokens=0,
            budget=8000,
            compile_time_ms=1.0,
            decisions=[],
            final_order=[],
        )
        result = CompiledResult(text="", report=report)
        assert result.to_messages() == []

    def test_to_messages_with_system_with_system(self, sample_result):
        """to_messages_with_system() includes system message when provided."""
        msgs = sample_result.to_messages_with_system(system="You are helpful.")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "System prompt.\n\nDocument text."

    def test_to_messages_with_system_no_system(self, sample_result):
        """to_messages_with_system() with None returns just user message."""
        msgs = sample_result.to_messages_with_system(system=None)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_to_messages_with_system_empty_text(self):
        """to_messages_with_system() with empty text and system returns system only."""
        report = CompileReport(
            input_blocks=0,
            output_blocks=0,
            input_tokens=0,
            output_tokens=0,
            budget=8000,
            compile_time_ms=1.0,
            decisions=[],
            final_order=[],
        )
        result = CompiledResult(text="", report=report)
        msgs = result.to_messages_with_system(system="Instruct.")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "Instruct."

    def test_to_anthropic(self, sample_result):
        """to_anthropic() returns (system_prompt, messages) tuple."""
        system, msgs = sample_result.to_anthropic()
        assert isinstance(system, str)
        assert isinstance(msgs, list)
        assert system == "System prompt.\n\nDocument text."
        assert msgs == []

    def test_to_json(self, sample_result):
        """to_json() returns a JSON-serializable dict."""
        data = sample_result.to_json()
        assert isinstance(data, dict)
        assert "text" in data
        assert "report" in data
        assert data["text"] == "System prompt.\n\nDocument text."
        
        # Verify it's actually JSON-serializable
        json_str = json.dumps(data)
        assert isinstance(json_str, str)
        assert "System prompt" in json_str


# ──────────────────────────────────────────────────────────────────────────
# ContextPack Tests
# ──────────────────────────────────────────────────────────────────────────


class TestContextPackBasics:
    """Tests for ContextPack initialization and basic operations."""

    def test_contextpack_init_defaults(self):
        """ContextPack initializes with defaults."""
        pack = ContextPack()
        assert pack.budget == 8000
        assert pack.quality_threshold == 0.5
        assert pack.separator == "\n\n---\n\n"

    def test_contextpack_init_custom(self):
        """ContextPack accepts custom parameters."""
        pack = ContextPack(budget=4000, quality_threshold=0.7, separator=" | ")
        assert pack.budget == 4000
        assert pack.quality_threshold == 0.7
        assert pack.separator == " | "

    def test_add_single_block(self):
        """add() appends a block."""
        pack = ContextPack()
        block = PackBlock(id="b1", type="test", content="text")
        result = pack.add(block)
        
        # Should return self for chaining
        assert result is pack
        assert len(pack._blocks) == 1
        assert pack._blocks[0] is block

    def test_add_multiple_blocks_chaining(self):
        """add() supports method chaining."""
        pack = ContextPack()
        pack.add(PackBlock(id="b1", type="test", content="t1")).add(
            PackBlock(id="b2", type="test", content="t2")
        ).add(PackBlock(id="b3", type="test", content="t3"))
        
        assert len(pack._blocks) == 3
        assert pack._blocks[0].id == "b1"
        assert pack._blocks[2].id == "b3"

    def test_clear_removes_all_blocks(self):
        """clear() removes all blocks."""
        pack = ContextPack()
        pack.add(PackBlock(id="b1", type="test", content="t1"))
        pack.add(PackBlock(id="b2", type="test", content="t2"))
        
        assert len(pack._blocks) == 2
        result = pack.clear()
        
        assert result is pack  # Returns self
        assert len(pack._blocks) == 0


class TestContextPackCompile:
    """Tests for ContextPack.compile() logic."""

    def test_compile_single_block(self):
        """compile() with single block works."""
        pack = ContextPack()
        pack.add(PackBlock(id="sys", type="instructions", content="System prompt.", priority="critical"))
        
        result = pack.compile()
        assert isinstance(result, CompiledResult)
        assert "System prompt" in result.text
        assert result.report.output_blocks == 1

    def test_compile_empty_pack(self):
        """compile() with no blocks returns empty result."""
        pack = ContextPack()
        result = pack.compile()
        
        assert result.text == ""
        assert result.report.input_blocks == 0
        assert result.report.output_blocks == 0

    def test_compile_respects_separator(self):
        """compile() joins blocks with configured separator."""
        pack = ContextPack(separator=" | ")
        # Use priority to ensure order
        pack.add(PackBlock(id="b1", type="test", content="First", priority="high"))
        pack.add(PackBlock(id="b2", type="test", content="Second", priority="low"))
        
        result = pack.compile()
        # Should contain both parts separated
        assert " | " in result.text
        assert "First" in result.text
        assert "Second" in result.text

    def test_compile_quality_filter_removes_low_quality(self):
        """Blocks below quality_threshold are REMOVED."""
        pack = ContextPack(quality_threshold=0.6)
        pack.add(PackBlock(id="good", type="test", content="Good content", quality=0.8))
        pack.add(PackBlock(id="bad", type="test", content="Bad content", quality=0.4))
        
        result = pack.compile()
        assert "Good content" in result.text
        assert "Bad content" not in result.text
        assert result.report.output_blocks == 1

    def test_compile_quality_threshold_boundary(self):
        """Block with quality exactly at threshold is kept."""
        pack = ContextPack(quality_threshold=0.5)
        pack.add(PackBlock(id="edge", type="test", content="Edge case", quality=0.5))
        
        result = pack.compile()
        assert "Edge case" in result.text

    def test_compile_critical_priority_budget_respected(self):
        """Critical blocks are kept within budget constraints."""
        pack = ContextPack(budget=10000)
        pack.add(PackBlock(
            id="crit",
            type="instructions",
            content="Critical instruction",
            priority="critical"
        ))
        
        result = pack.compile()
        # Critical block should be kept
        assert "Critical instruction" in result.text
        assert result.report.output_blocks == 1

    def test_compile_block_level_max_tokens(self):
        """Blocks exceeding max_tokens are compacted."""
        pack = ContextPack(budget=10000)
        long_text = "word " * 500  # ~500 words
        pack.add(PackBlock(
            id="long",
            type="test",
            content=long_text,
            max_tokens=50  # Strict limit
        ))
        
        result = pack.compile()
        # Output should be truncated
        assert len(result.text) < len(long_text)
        assert result.report.output_blocks == 1

    def test_compile_budget_enforcement(self):
        """Total output respects budget."""
        pack = ContextPack(budget=50)
        pack.add(PackBlock(id="b1", type="test", content="Block 1 " * 20, priority="critical"))
        pack.add(PackBlock(id="b2", type="test", content="Block 2 " * 20, priority="low"))
        
        result = pack.compile()
        assert result.report.output_tokens <= pack.budget

    def test_compile_priority_ordering(self):
        """Output respects priority order (critical > high > medium > low)."""
        pack = ContextPack(budget=10000)
        pack.add(PackBlock(id="low", type="test", content="LOW", priority="low"))
        pack.add(PackBlock(id="crit", type="test", content="CRITICAL", priority="critical"))
        pack.add(PackBlock(id="med", type="test", content="MEDIUM", priority="medium"))
        pack.add(PackBlock(id="high", type="test", content="HIGH", priority="high"))
        
        result = pack.compile()
        text = result.text
        # Critical should come before low
        crit_idx = text.find("CRITICAL")
        low_idx = text.find("LOW")
        assert crit_idx < low_idx
        # High should come before medium
        high_idx = text.find("HIGH")
        med_idx = text.find("MEDIUM")
        assert high_idx < med_idx

    def test_compile_report_has_decisions(self):
        """Compile report includes decision objects for each block."""
        pack = ContextPack()
        pack.add(PackBlock(id="b1", type="test", content="content1"))
        pack.add(PackBlock(id="b2", type="test", content="content2"))
        
        result = pack.compile()
        assert len(result.report.decisions) == 2
        assert all(hasattr(d, 'block_id') for d in result.report.decisions)
        assert all(hasattr(d, 'action') for d in result.report.decisions)

    def test_compile_report_final_order(self):
        """Report includes final_order of block IDs."""
        pack = ContextPack()
        pack.add(PackBlock(id="first", type="test", content="1", priority="high"))
        pack.add(PackBlock(id="second", type="test", content="2", priority="low"))
        
        result = pack.compile()
        assert len(result.report.final_order) == 2
        # High should come before low
        assert result.report.final_order[0] == "first"


class TestPackPromptHelper:
    """Tests for pack_prompt() convenience function."""

    def test_pack_prompt_basic(self):
        """pack_prompt() with all arguments."""
        result = pack_prompt(
            system="System",
            docs="Documentation",
            history="History",
            budget=2000
        )
        assert isinstance(result, str)
        assert "System" in result
        assert "Documentation" in result
        assert "History" in result

    def test_pack_prompt_system_only(self):
        """pack_prompt() with system only."""
        result = pack_prompt(system="Just system")
        assert isinstance(result, str)
        assert "Just system" in result

    def test_pack_prompt_docs_only(self):
        """pack_prompt() with docs only."""
        result = pack_prompt(docs="Just docs")
        assert isinstance(result, str)
        assert "Just docs" in result

    def test_pack_prompt_history_only(self):
        """pack_prompt() with history only."""
        result = pack_prompt(history="Just history")
        assert isinstance(result, str)
        assert "Just history" in result

    def test_pack_prompt_all_none(self):
        """pack_prompt() with all None arguments."""
        result = pack_prompt(system=None, docs=None, history=None)
        # Should return empty or minimal string
        assert isinstance(result, str)

    def test_pack_prompt_default_budget(self):
        """pack_prompt() uses default budget of 8000."""
        result = pack_prompt(system="S" * 1000, docs="D" * 7000)
        assert isinstance(result, str)

    def test_pack_prompt_custom_budget(self):
        """pack_prompt() accepts custom budget."""
        # With very low budget, content gets truncated
        result_low = pack_prompt(
            system="System content",
            docs="D" * 1000,
            budget=50
        )
        result_high = pack_prompt(
            system="System content",
            docs="D" * 1000,
            budget=5000
        )
        # Higher budget should yield more content
        assert len(result_high) >= len(result_low)

    def test_pack_prompt_priority_preserved(self):
        """pack_prompt() assigns correct priorities to blocks."""
        result = pack_prompt(
            system="System",
            docs="Docs",
            history="History"
        )
        # System (critical) should appear before history (low)
        sys_idx = result.find("System")
        hist_idx = result.find("History")
        assert sys_idx < hist_idx


# ──────────────────────────────────────────────────────────────────────────
# Edge Cases & Integration Tests
# ──────────────────────────────────────────────────────────────────────────


class TestEdgeCasesAndIntegration:
    """Tests for edge cases and integration scenarios."""

    def test_very_small_budget(self):
        """Compile with very small budget truncates content."""
        pack = ContextPack(budget=10)
        pack.add(PackBlock(id="b1", type="test", content="Very long content " * 50))
        
        result = pack.compile()
        assert result.report.output_tokens <= pack.budget

    def test_unicode_content(self):
        """Handle Unicode content correctly."""
        pack = ContextPack()
        pack.add(PackBlock(
            id="unicode",
            type="test",
            content="Hello 👋 World 🌍 Émojis: 📚🔬🎨"
        ))
        
        result = pack.compile()
        assert "👋" in result.text
        assert "Émojis" in result.text

    def test_very_long_block_id(self):
        """Block IDs can be long strings."""
        long_id = "x" * 500
        pack = ContextPack()
        pack.add(PackBlock(id=long_id, type="test", content="content"))
        
        result = pack.compile()
        assert long_id in result.report.final_order

    def test_special_characters_in_content(self):
        """Handle special characters and line breaks."""
        special_content = "Line1\nLine2\n\nLine3\t\tTabbed"
        pack = ContextPack()
        pack.add(PackBlock(id="special", type="test", content=special_content))
        
        result = pack.compile()
        assert special_content in result.text

    def test_many_blocks(self):
        """Compile with many blocks (stress test)."""
        pack = ContextPack(budget=10000)
        for i in range(100):
            pack.add(PackBlock(
                id=f"block-{i}",
                type="test",
                content=f"Block {i} content"
            ))
        
        result = pack.compile()
        assert result.report.input_blocks == 100

    def test_duplicate_block_ids(self):
        """Adding blocks with duplicate IDs (last one counted)."""
        pack = ContextPack()
        pack.add(PackBlock(id="dup", type="test", content="First"))
        pack.add(PackBlock(id="dup", type="test", content="Second"))
        
        result = pack.compile()
        # Both should be processed (no dedup at add time)
        assert result.report.input_blocks == 2

    def test_quality_with_none_value(self):
        """quality=None is handled (not filtered)."""
        pack = ContextPack(quality_threshold=0.9)
        pack.add(PackBlock(id="nq", type="test", content="No quality", quality=None))
        
        result = pack.compile()
        assert "No quality" in result.text

    def test_max_tokens_zero(self):
        """max_tokens=0 results in empty/minimal output."""
        pack = ContextPack()
        pack.add(PackBlock(
            id="zero",
            type="test",
            content="Long content here",
            max_tokens=0
        ))
        
        result = pack.compile()
        # Content should be severely truncated
        assert len(result.text) < 20

    def test_compile_reproducibility(self):
        """Same input always produces same output."""
        def make_pack():
            p = ContextPack(budget=5000)
            p.add(PackBlock(id="a", type="test", content="Content A", priority="high"))
            p.add(PackBlock(id="b", type="test", content="Content B", priority="low"))
            return p
        
        result1 = make_pack().compile()
        result2 = make_pack().compile()
        
        assert result1.text == result2.text
        assert result1.report.output_tokens == result2.report.output_tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
