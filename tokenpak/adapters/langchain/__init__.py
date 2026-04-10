"""langchain-tokenpak: TokenPak integration for LangChain."""

from .converters import Block, doc_to_block, block_to_doc
from .retrievers import TokenPakRetriever
from .memory import TokenPakMemory
from .context import TokenPakContextManager
# Re-export from langchain.py module for tests that import LangChainAdapter from this package
from tokenpak.adapters.langchain_adapter import LangChainAdapter, _normalise_messages  # noqa: F401

__all__ = [
    "Block",
    "doc_to_block",
    "block_to_doc",
    "TokenPakRetriever",
    "TokenPakMemory",
    "TokenPakContextManager",
    "LangChainAdapter",
    "_normalise_messages",
]
