# llamaindex-tokenpak

TokenPak integration for LlamaIndex — automatic context compression for RAG pipelines.

Reduces token costs by 40-60% on retrieved nodes without sacrificing quality.

[![PyPI version](https://img.shields.io/pypi/v/llamaindex-tokenpak)](https://pypi.org/project/llamaindex-tokenpak/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Installation

```bash
pip install llamaindex-tokenpak
```

---

## Quick Start

### Synthesizer with compression

```python
from llamaindex_tokenpak import TokenPakSynthesizer

# Compress retrieved nodes before sending to LLM
synthesizer = TokenPakSynthesizer(budget=4000)

query_engine = index.as_query_engine(
    synthesizer=synthesizer,
)

response = query_engine.query("What is context compression?")
```

### Query engine wrapper

```python
from llamaindex_tokenpak import TokenPakQueryEngine

base_engine = index.as_query_engine()
tp_engine = TokenPakQueryEngine(
    query_engine=base_engine,
    budget=4000,
)

response = tp_engine.query("Summarize the key points")
await tp_engine.aquery("Async version also supported")
```

### Index with compression

```python
from llamaindex_tokenpak import TokenPakIndex

index = TokenPakIndex.from_documents(
    documents,
    budget=2000,   # token budget for retrieved nodes
)
query_engine = index.as_query_engine()
```

---

## What is TokenPak?

TokenPak is an open protocol for AI context optimization. It compresses context blocks to fit within token budgets while keeping the highest-priority content intact.

Learn more: https://github.com/kaywhy331/tokenpak

---

## API Reference

### `TokenPakSynthesizer`

```python
class TokenPakSynthesizer:
    def __init__(
        self,
        budget: int = 4000,       # max tokens for context nodes
        keep_headers: bool = True, # preserve markdown structure
    ) -> None: ...

    def synthesize(
        self,
        query: str,
        nodes: List[Dict[str, Any]],
    ) -> str: ...
```

### `TokenPakQueryEngine`

```python
class TokenPakQueryEngine:
    def __init__(
        self,
        query_engine: Any,
        budget: int = 4000,
    ) -> None: ...

    def query(self, query_str: str, **kwargs) -> Dict[str, Any]: ...
    async def aquery(self, query_str: str, **kwargs) -> Dict[str, Any]: ...
```

### `TokenPakIndex`

```python
class TokenPakIndex:
    def __init__(self, index: Any, budget: int = 2000) -> None: ...

    @classmethod
    def from_documents(
        cls,
        documents: List[Dict[str, Any]],
        budget: int = 2000,
        **kwargs,
    ) -> "TokenPakIndex": ...

    def as_query_engine(self, **kwargs) -> Any: ...
```

### Node Converters

```python
from llamaindex_tokenpak import (
    llamaindex_node_to_block,
    block_to_llamaindex_node,
    llamaindex_nodes_to_blocks,
    blocks_to_llamaindex_nodes,
)

# Single node ↔ block
block = llamaindex_node_to_block(node, block_id="optional-id")
node  = block_to_llamaindex_node(block)

# Batch conversion
blocks = llamaindex_nodes_to_blocks(nodes)
nodes  = blocks_to_llamaindex_nodes(blocks)
```

---

## Performance

Typical savings on RAG pipelines:

| Content Type      | Savings | Quality Impact |
|-------------------|---------|----------------|
| Narrative text    | 50-70%  | Minimal        |
| Code blocks       | 10-20%  | Low (preserved)|
| Structured data   | 30-40%  | Minimal        |
| Recent context    | 0%      | None (kept)    |

---

## Support

- Issues: https://github.com/kaywhy331/tokenpak/issues
- Discussions: https://github.com/kaywhy331/tokenpak/discussions

## License

MIT
