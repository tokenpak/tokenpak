"""Regression tests for the `tokenpak retrieval test` CLI argument plumbing.

Guards the contract between ``cmd_retrieval_test`` and
``HybridRetriever.search``: the handler must pass the query *string* and
``top_k`` straight through.

A prior regression wrapped the query in a ``RetrievalQuery`` and dropped
``top_k``. ``HybridRetriever.search`` re-wraps its ``query_text`` argument into
its own ``RetrievalQuery``, so BM25 tokenization received a dataclass instead of
a ``str``. Because ``RetrievalQuery`` is an unfrozen (unhashable) dataclass and
``_tokenize`` is ``lru_cache``-decorated, the wired, advertised verb crashed with
``TypeError: unhashable type`` whenever the index was configured (and silently
returned ``[]`` otherwise) — i.e. it could never return a correct result.
"""

import argparse

import pytest

from tokenpak import _cli_core
from tokenpak.vault.retrieval.base import RetrievalQuery
from tokenpak.vault.retrieval.bm25 import _tokenize
from tokenpak.vault.retrieval.hybrid import HybridRetriever


def _args(**kw):
    ns = argparse.Namespace(query="hello world", top_k=3, json=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_retrieval_test_passes_str_and_top_k(monkeypatch, capsys):
    """The handler must hand search() a str query and the requested top_k."""
    captured = {}

    async def fake_search(self, query_text, top_k=5):
        captured["query_text"] = query_text
        captured["top_k"] = top_k
        return []

    monkeypatch.setattr(HybridRetriever, "search", fake_search)

    _cli_core.cmd_retrieval_test(_args(top_k=7))

    # The exact regression: a *string* must reach search(), never a RetrievalQuery.
    assert isinstance(captured["query_text"], str)
    assert captured["query_text"] == "hello world"
    # Secondary regression: --top-k must be threaded through, not dropped.
    assert captured["top_k"] == 7


def test_tokenize_accepts_str_rejects_query_dataclass():
    """Documents the crash mechanism: the tokenizer accepts a str and the
    cache layer rejects the unhashable RetrievalQuery the bug used to pass."""
    # The fixed path: a plain string tokenizes cleanly.
    assert isinstance(_tokenize("hello world"), list)
    # The old double-wrap path: a RetrievalQuery is unhashable, so the
    # lru_cache key computation raises before _tokenize ever runs.
    with pytest.raises(TypeError):
        _tokenize(RetrievalQuery(text="x"))
