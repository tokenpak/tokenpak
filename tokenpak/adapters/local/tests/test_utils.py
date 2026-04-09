"""Tests for utils module."""

from tokenpak_local.utils import (
    Block,
    TokenPak,
    blocks_from_texts,
    pack_from_blocks,
)


class TestBlock:
    def test_default_block_type(self):
        b = Block(content="Hello world")
        assert b.type == "evidence"

    def test_token_estimation(self):
        b = Block(content="a" * 400)
        assert b.tokens == 100  # 400 // 4

    def test_quality_set(self):
        b = Block(content="test", quality=0.85)
        assert b.quality == 0.85

    def test_to_dict(self):
        b = Block(type="knowledge", content="facts", quality=0.9)
        d = b.to_dict()
        assert d["type"] == "knowledge"
        assert d["content"] == "facts"
        assert d["quality"] == 0.9

    def test_metadata_default_empty(self):
        b = Block(content="test")
        assert b.metadata == {}

    def test_provenance_default_empty(self):
        b = Block(content="test")
        assert b.provenance == {}


class TestTokenPak:
    def test_add_blocks(self):
        pack = TokenPak()
        b = Block(content="hello")
        pack.add(b)
        assert len(pack._blocks) == 1

    def test_to_messages_no_instructions(self):
        pack = TokenPak()
        pack.add(Block(type="evidence", content="Earth orbits the Sun"))
        msgs = pack.to_messages()
        assert any(m["role"] == "system" for m in msgs)
        system = next(m for m in msgs if m["role"] == "system")
        assert "Earth orbits the Sun" in system["content"]

    def test_to_messages_with_instructions(self):
        pack = TokenPak(instructions="Answer concisely.")
        pack.add(Block(content="data"))
        msgs = pack.to_messages()
        system = next(m for m in msgs if m["role"] == "system")
        assert "Answer concisely." in system["content"]

    def test_total_tokens(self):
        pack = TokenPak()
        pack.add(Block(content="a" * 400))  # 100 tokens
        pack.add(Block(content="b" * 200))  # 50 tokens
        assert pack.total_tokens == 150

    def test_compile_returns_self(self):
        pack = TokenPak()
        result = pack.compile()
        assert result is pack

    def test_budget_set(self):
        pack = TokenPak(budget=8000)
        assert pack.budget == 8000


class TestBlocksFromTexts:
    def test_basic(self):
        blocks = blocks_from_texts(["doc1", "doc2"])
        assert len(blocks) == 2
        assert blocks[0].content == "doc1"
        assert blocks[1].content == "doc2"

    def test_default_block_type(self):
        blocks = blocks_from_texts(["test"])
        assert blocks[0].type == "evidence"

    def test_custom_block_type(self):
        blocks = blocks_from_texts(["test"], block_type="knowledge")
        assert blocks[0].type == "knowledge"

    def test_metadata_list(self):
        blocks = blocks_from_texts(
            ["doc1", "doc2"], metadata_list=[{"source": "a"}, {"source": "b"}]
        )
        assert blocks[0].metadata["source"] == "a"
        assert blocks[1].metadata["source"] == "b"

    def test_provenance_has_index(self):
        blocks = blocks_from_texts(["doc"])
        assert blocks[0].provenance["index"] == 0
        assert blocks[0].provenance["source_type"] == "text"

    def test_quality_set(self):
        blocks = blocks_from_texts(["doc"], quality=0.75)
        assert blocks[0].quality == 0.75

    def test_empty_list(self):
        blocks = blocks_from_texts([])
        assert blocks == []


class TestPackFromBlocks:
    def test_basic(self):
        blocks = [Block(content="a"), Block(content="b")]
        pack = pack_from_blocks(blocks)
        assert len(pack._blocks) == 2

    def test_instructions_set(self):
        pack = pack_from_blocks([], instructions="Do the thing.")
        assert pack.instructions == "Do the thing."

    def test_budget_set(self):
        pack = pack_from_blocks([], budget=5000)
        assert pack.budget == 5000

    def test_messages_include_blocks(self):
        blocks = [Block(type="evidence", content="context")]
        pack = pack_from_blocks(blocks)
        msgs = pack.to_messages()
        system = next(m for m in msgs if m["role"] == "system")
        assert "context" in system["content"]
