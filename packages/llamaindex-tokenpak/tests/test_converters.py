"""Tests for LlamaIndex ↔ TokenPak Block conversion."""

import pytest
from llamaindex_tokenpak import (
    LlamaBlock,
    llamaindex_node_to_block,
    block_to_llamaindex_node,
    llamaindex_nodes_to_blocks,
    blocks_to_llamaindex_nodes,
)


# --- Fixtures ---

def make_node(text="Sample text for testing.", score=0.9, source="test.md"):
    return {
        "id": "node_abc123",
        "text": text,
        "metadata": {"file_name": source, "page": 1},
        "score": score,
    }


# --- LlamaBlock tests ---

class TestLlamaBlock:
    def test_auto_token_count(self):
        block = LlamaBlock(id="b1", content="Hello world this is content.")
        assert block.tokens > 0

    def test_to_tokenpak_dict(self):
        block = LlamaBlock(id="b1", content="Test content", quality=0.8)
        d = block.to_tokenpak_dict()
        assert d["type"] == "evidence"
        assert d["content"] == "Test content"
        assert d["quality"] == 0.8
        assert "tokens" in d

    def test_to_llamaindex_node(self):
        block = LlamaBlock(id="b1", content="Test", quality=0.7)
        node = block.to_llamaindex_node()
        assert node["id"] == "b1"
        assert node["text"] == "Test"
        assert "_tokenpak_quality" in node["metadata"]

    def test_custom_block_type(self):
        block = LlamaBlock(id="b2", content="Test", block_type="knowledge")
        assert block.block_type == "knowledge"
        assert block.to_tokenpak_dict()["type"] == "knowledge"


# --- Converter function tests ---

class TestNodeToBlock:
    def test_dict_node_basic(self):
        node = make_node()
        block = llamaindex_node_to_block(node)
        assert block.id == "node_abc123"
        assert "Sample text" in block.content
        assert block.quality == pytest.approx(0.9)

    def test_dict_node_metadata_extraction(self):
        node = make_node(source="docs/README.md")
        block = llamaindex_node_to_block(node)
        # file_name should be in provenance, not metadata
        assert "file_name" in block.provenance
        assert block.provenance["file_name"] == "docs/README.md"

    def test_custom_block_type(self):
        node = make_node()
        block = llamaindex_node_to_block(node, block_type="knowledge")
        assert block.block_type == "knowledge"

    def test_custom_block_id(self):
        node = make_node()
        block = llamaindex_node_to_block(node, block_id="custom_id")
        assert block.id == "custom_id"

    def test_missing_id_gets_hash(self):
        node = {"text": "No ID node", "metadata": {}}
        block = llamaindex_node_to_block(node)
        assert block.id.startswith("node_")
        assert len(block.id) > 5

    def test_empty_text(self):
        node = {"text": "", "metadata": {}, "id": "empty"}
        block = llamaindex_node_to_block(node)
        assert block.content == ""
        assert block.id == "empty"

    def test_score_preserved(self):
        node = make_node(score=0.42)
        block = llamaindex_node_to_block(node)
        assert block.quality == pytest.approx(0.42)

    def test_node_with_score_wrapper(self):
        """Simulate LlamaIndex NodeWithScore object."""
        class MockTextNode:
            text = "Inner text"
            metadata = {"source": "wiki"}
            node_id = "inner_node"

        class MockNodeWithScore:
            node = MockTextNode()
            score = 0.77

        block = llamaindex_node_to_block(MockNodeWithScore())
        assert block.quality == pytest.approx(0.77)
        assert "Inner text" in block.content


class TestBlockToNode:
    def test_basic_roundtrip(self):
        node = make_node()
        block = llamaindex_node_to_block(node)
        out_node = block_to_llamaindex_node(block)
        assert out_node["id"] == block.id
        assert out_node["text"] == block.content

    def test_extra_metadata_passed(self):
        block = LlamaBlock(id="b1", content="Test")
        out = block_to_llamaindex_node(block, rank=1)
        assert out["metadata"]["rank"] == 1


class TestBatchConversion:
    def test_nodes_to_blocks(self):
        nodes = [make_node(text=f"Text {i}", score=0.5 + i * 0.1) for i in range(5)]
        blocks = llamaindex_nodes_to_blocks(nodes)
        assert len(blocks) == 5
        for block in blocks:
            assert isinstance(block, LlamaBlock)

    def test_blocks_to_nodes(self):
        blocks = [LlamaBlock(id=f"b{i}", content=f"Content {i}") for i in range(3)]
        nodes = blocks_to_llamaindex_nodes(blocks)
        assert len(nodes) == 3
        for node in nodes:
            assert "text" in node
            assert "id" in node
