import pytest
from langchain_tokenpak.retrievers import TokenPakRetriever


class MockDoc:
    def __init__(self, content, metadata=None):
        self.page_content = content
        self.metadata = metadata or {}


class MockRetriever:
    def __init__(self, docs):
        self._docs = docs

    def get_relevant_documents(self, query):
        return self._docs


def test_retriever_basic():
    docs = [MockDoc("Document " + str(i) + " content") for i in range(5)]
    retriever = TokenPakRetriever(retriever=MockRetriever(docs), budget=10000)
    result = retriever.get_relevant_documents("test")
    assert len(result) == 5


def test_retriever_budget_respected():
    docs = [MockDoc("A" * 200) for _ in range(10)]
    retriever = TokenPakRetriever(retriever=MockRetriever(docs), budget=100)
    result = retriever.get_relevant_documents("test")
    assert len(result) <= 3


def test_retriever_estimate_tokens():
    retriever = TokenPakRetriever(retriever=None, budget=1000)
    tokens = retriever._estimate_tokens("hello world")
    assert 1 <= tokens <= 20


def test_retriever_priority_ordering():
    docs = [
        MockDoc("low priority", {"score": 0.1}),
        MockDoc("high priority", {"score": 0.9}),
    ]
    retriever = TokenPakRetriever(retriever=MockRetriever(docs), budget=50)
    result = retriever.get_relevant_documents("test")
    assert any("high" in getattr(d, "page_content", "") for d in result)


@pytest.mark.asyncio
async def test_retriever_async():
    docs = [MockDoc("async content")]
    retriever = TokenPakRetriever(retriever=MockRetriever(docs), budget=1000)
    result = await retriever.aget_relevant_documents("test")
    assert len(result) == 1
