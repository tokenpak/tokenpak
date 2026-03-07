"""Tests for TokenPakRetriever."""

import pytest
from langchain_tokenpak import TokenPakRetriever


class MockRetriever:
    """Mock retriever for testing."""
    
    def get_relevant_documents(self, query: str):
        return [
            {
                "page_content": "A" * 1000,
                "metadata": {"source": "test", "score": 0.9},
            }
        ]


def test_retriever_compression():
    """Test document compression."""
    mock_retriever = MockRetriever()
    retriever = TokenPakRetriever(
        retriever=mock_retriever,
        budget=100,
    )
    
    docs = retriever.get_relevant_documents("test query")
    assert len(docs) > 0
    assert len(docs[0]["page_content"]) < 1000
