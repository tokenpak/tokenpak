"""Tests for document/block conversion."""

from langchain_tokenpak import (
    langchain_document_to_block,
    block_to_langchain_document,
)


def test_doc_to_block():
    """Test Document → Block conversion."""
    doc = {
        "page_content": "Test content",
        "metadata": {"source": "test"},
    }
    
    block = langchain_document_to_block(doc)
    assert block.content == "Test content"
    assert block.metadata["source"] == "test"


def test_block_to_doc():
    """Test Block → Document conversion."""
    from langchain_tokenpak import Block
    
    block = Block(
        id="test",
        content="Test",
        metadata={"key": "value"},
    )
    
    doc = block_to_langchain_document(block)
    assert doc["page_content"] == "Test"
    assert doc["metadata"]["block_id"] == "test"
