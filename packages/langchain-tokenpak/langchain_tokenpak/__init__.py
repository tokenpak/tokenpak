"""
langchain-tokenpak

TokenPak integration for LangChain — automatic context compression for RAG and chat chains.

Quick Start:
    from langchain_tokenpak import TokenPakRetriever, TokenPakMemory

    # Compress retrieved documents
    retriever = TokenPakRetriever(base_retriever, budget=4000)
    docs = retriever.get_relevant_documents(query)

    # Compress chat history
    memory = TokenPakMemory(max_tokens=2000)
    memory.add_user_message("Hello!")
    messages = memory.messages  # auto-compressed if over budget
"""

from .converters import (
    Block,
    langchain_document_to_block,
    block_to_langchain_document,
    langchain_documents_to_blocks,
    blocks_to_langchain_documents,
)
from .retrievers import TokenPakRetriever
from .memory import TokenPakMemory
from .context import TokenPakContextManager
from .langgraph import TokenPakState

__version__ = "0.1.0"
__all__ = [
    "Block",
    "TokenPakRetriever",
    "TokenPakMemory",
    "TokenPakContextManager",
    "langchain_document_to_block",
    "block_to_langchain_document",
    "langchain_documents_to_blocks",
    "blocks_to_langchain_documents",
    "TokenPakState",
]
