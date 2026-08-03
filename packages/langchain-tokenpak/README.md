# langchain-tokenpak

TokenPak integration for LangChain — automatic context compression for RAG and chat chains.

Reduces token costs on retrieved documents and chat history while preserving recent context — measure your savings with `tokenpak savings`.

## Installation

```bash
pip install langchain-tokenpak
```

## Quick Start

### Compress Retrieved Documents

```python
from langchain_tokenpak import TokenPakRetriever

# Wrap your existing retriever
base_retriever = vector_store.as_retriever()
tp_retriever = TokenPakRetriever(
    retriever=base_retriever,
    budget=4000,  # max tokens for all docs
    keep_headers=True,  # preserve markdown structure
)

# Use in your chain
compressed_docs = tp_retriever.get_relevant_documents(query)
```

### Compress Chat History

```python
from langchain_tokenpak import TokenPakMemory

# Replace your message history
memory = TokenPakMemory(
    max_tokens=2000,
    keep_recent_turns=4,  # always preserve last 4 exchanges
)

# Add messages as usual
memory.add_user_message("What is dependency injection?")
memory.add_ai_message("It's a design pattern...")

# Messages auto-compress when over budget
messages = memory.messages
```

### Coordinate Budgets

```python
from langchain_tokenpak import TokenPakContextManager

# Split a total budget between documents and chat
ctx_mgr = TokenPakContextManager(
    total_budget=8000,
    doc_ratio=0.7,  # 70% for docs, 30% for memory
)

doc_budget = ctx_mgr.document_budget()      # 5600
memory_budget = ctx_mgr.memory_budget()     # 2400
```

## What is TokenPak?

TokenPak is an open protocol for AI context optimization. It defines:

- **Blocks**: The fundamental unit of context (documents, chunks, messages)
- **Recipes**: Compression strategies for different content types
- **Budgets**: Token limits that guide compression decisions

Learn more: https://github.com/tokenpak/tokenpak

## Features

### TokenPakRetriever

Wraps any LangChain retriever and automatically compresses results:

```python
retriever = TokenPakRetriever(
    retriever=base_retriever,
    budget=4000,
    keep_headers=True,
    keep_code=True,
    min_score=0.5,  # filter low-relevance docs
)
```

- **budget**: Max tokens for all retrieved documents
- **keep_headers**: Preserve markdown/HTML structure
- **keep_code**: Don't compress code blocks
- **min_score**: Minimum relevance threshold (0-1)

### TokenPakMemory

Automatic chat history compression with priority for recent turns:

```python
memory = TokenPakMemory(
    max_tokens=2000,
    keep_recent_turns=4,
    session_id="user_123",
)

memory.add_user_message("...")
memory.add_ai_message("...")
messages = memory.messages  # auto-compressed
memory.clear()
```

### TokenPakContextManager

Intelligent budget allocation for multi-component chains:

```python
ctx_mgr = TokenPakContextManager(
    total_budget=8000,
    doc_ratio=0.7,
    min_memory_tokens=500,
)

# Get dynamic budgets based on actual usage
budgets = ctx_mgr.adjust_budget(
    doc_tokens=5000,
    memory_tokens=2500,
)
```

## Conversions

Convert between LangChain Documents and TokenPak Blocks:

```python
from langchain_tokenpak import (
    langchain_document_to_block,
    block_to_langchain_document,
)

# Document → Block
block = langchain_document_to_block({
    "page_content": "...",
    "metadata": {"source": "..."},
})

# Block → Document
doc = block_to_langchain_document(block)
```

## LangGraph Integration

Manage state in multi-agent workflows:

```python
from langchain_tokenpak.langgraph import TokenPakState

state = TokenPakState(max_tokens=4000)
state.append_message("agent_1", "response...")
state.append_message("agent_2", "response...")

# Messages auto-compress when over budget
messages = state.messages
```

## API Reference

### TokenPakRetriever

```python
class TokenPakRetriever:
    def __init__(
        self,
        retriever: Any,
        budget: int = 4000,
        keep_headers: bool = True,
        keep_code: bool = True,
        min_score: float = 0.0,
    ) -> None: ...

    def get_relevant_documents(self, query: str) -> List[Dict[str, Any]]: ...
    async def aget_relevant_documents(self, query: str) -> List[Dict[str, Any]]: ...
    def get_compression_stats(self) -> Dict[str, Any]: ...
```

### TokenPakMemory

```python
class TokenPakMemory:
    def __init__(
        self,
        max_tokens: int = 2000,
        keep_recent_turns: int = 4,
        session_id: Optional[str] = None,
    ) -> None: ...

    def add_user_message(self, content: str) -> None: ...
    def add_ai_message(self, content: str) -> None: ...
    def add_message(self, role: str, content: str) -> None: ...

    @property
    def messages(self) -> List[Dict[str, Any]]: ...

    def clear(self) -> None: ...
```

### TokenPakContextManager

```python
class TokenPakContextManager:
    def __init__(
        self,
        total_budget: int = 8000,
        doc_ratio: float = 0.7,
        min_memory_tokens: int = 500,
    ) -> None: ...

    def document_budget(self) -> int: ...
    def memory_budget(self) -> int: ...
    def adjust_budget(
        self,
        doc_tokens: int,
        memory_tokens: int,
    ) -> Dict[str, int]: ...
```

## Examples

See [examples/](./examples/) for complete working examples:

- `rag_chain.py` — RAG pipeline with compression
- `chat_history.py` — Chat with automatic memory management
- `multi_agent.py` — LangGraph workflows with state compression
- `budget_allocation.py` — Dynamic budget coordination

## Performance

Relative savings by component (measure your own with `tokenpak savings`):

| Component | Relative savings | Quality Impact |
|-----------|---------|---|
| Retrieved docs (RAG) | High | Minimal (keep headers, code) |
| Chat history | Moderate | Low (preserve recent turns) |
| Combined chain | Moderate–High | Low (coordinated budgets) |

Compressibility varies by content type:

- **Narrative text**: high compression
- **Code**: low compression (preserved for quality)
- **Structured data**: moderate compression
- **Recent context**: not compressed (preserved entirely)

## Documentation

- **Docs**: https://github.com/tokenpak/tokenpak
- **Protocol**: https://tokenpak.dev/protocol

## Support

- Issues: https://github.com/tokenpak/tokenpak/issues
- Discussions: https://github.com/tokenpak/tokenpak/discussions
- Email: support@tokenpak.dev

## License

Apache-2.0
