"""Tests for langfuse_tokenpak.visualization"""

import pytest
from langfuse_tokenpak.visualization import (
    block_to_dict,
    blocks_to_metadata,
    ascii_block_summary,
    BLOCK_TYPE_ICONS,
)


def make_block(btype="knowledge", tokens=100, priority="medium", compacted=False, bid="blk1", source=None):
    return {
        "id": bid,
        "type": btype,
        "tokens": tokens,
        "priority": priority,
        "compacted": compacted,
        "source": source,
    }


class TestBlockToDict:
    def test_dict_block(self):
        b = make_block(btype="knowledge", tokens=420, priority="high", compacted=True)
        result = block_to_dict(b)
        assert result["type"] == "knowledge"
        assert result["tokens"] == 420
        assert result["compacted"] is True
        assert result["icon"] == BLOCK_TYPE_ICONS["knowledge"]

    def test_unknown_type_gets_default_icon(self):
        b = make_block(btype="weird_type")
        result = block_to_dict(b)
        assert result["icon"] == "📄"

    def test_object_block(self):
        class Block:
            id = "obj1"
            type = "instructions"
            tokens = 150
            priority = "critical"
            compacted = False
            source = None

        result = block_to_dict(Block())
        assert result["type"] == "instructions"
        assert result["icon"] == BLOCK_TYPE_ICONS["instructions"]


class TestBlocksToMetadata:
    def test_basic(self):
        blocks = [
            make_block("instructions", 150, "critical"),
            make_block("knowledge", 420, "high", True),
            make_block("evidence", 310, "medium"),
        ]
        meta = blocks_to_metadata(blocks, budget=8000)
        assert meta["block_count"] == 3
        assert meta["total_tokens"] == 880
        assert meta["budget"] == 8000
        assert meta["compacted_blocks"] == 1
        assert "knowledge" in meta["type_distribution"]
        assert meta["utilization_pct"] == round(880 / 8000 * 100, 1)

    def test_no_budget(self):
        blocks = [make_block("memory", 50)]
        meta = blocks_to_metadata(blocks)
        assert "budget" not in meta
        assert meta["total_tokens"] == 50

    def test_empty(self):
        meta = blocks_to_metadata([])
        assert meta["block_count"] == 0
        assert meta["total_tokens"] == 0


class TestAsciiSummary:
    def test_renders(self):
        blocks = [
            make_block("instructions", 150, "critical"),
            make_block("knowledge", 420, "high", True, source="pinecone"),
        ]
        out = ascii_block_summary(blocks, budget=8000)
        assert "TokenPak Pack" in out
        assert "570/8000 tokens" in out
        assert "[compacted]" in out
        assert "src:pinecone" in out
        assert "└──" in out
