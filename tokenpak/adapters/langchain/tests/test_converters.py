from langchain_tokenpak.converters import (
    Block,
    doc_to_block,
    block_to_doc,
    docs_to_blocks,
    blocks_to_docs,
)


class MockDoc:
    def __init__(self, content, metadata=None):
        self.page_content = content
        self.metadata = metadata or {}


def test_block_creation():
    block = Block(content="hello world", priority=0.8)
    assert block.content == "hello world"
    assert block.priority == 0.8


def test_block_to_dict():
    block = Block(content="test", metadata={"k": "v"}, priority=0.5, source="wiki")
    d = block.to_dict()
    assert d["content"] == "test"
    assert d["priority"] == 0.5


def test_doc_to_block():
    doc = MockDoc("Paris is in France.", {"source": "wiki", "score": 0.9})
    block = doc_to_block(doc)
    assert block.content == "Paris is in France."
    assert block.priority == 0.9


def test_doc_to_block_default_priority():
    doc = MockDoc("Simple content.")
    block = doc_to_block(doc)
    assert block.priority == 1.0


def test_block_to_doc():
    block = Block(content="hello", metadata={}, source="test", priority=0.7)
    doc = block_to_doc(block)
    assert doc.page_content == "hello"
    assert doc.metadata["tokenpak_priority"] == 0.7


def test_docs_to_blocks_list():
    docs = [MockDoc(f"content {i}") for i in range(3)]
    blocks = docs_to_blocks(docs)
    assert len(blocks) == 3


def test_blocks_to_docs_list():
    blocks = [Block(content=f"item {i}") for i in range(3)]
    docs = blocks_to_docs(blocks)
    assert len(docs) == 3
