# llamaindex-tokenpak

TokenPak integration for LlamaIndex — automatic context compression for RAG pipelines.

Reduces token costs by 40-60% on retrieved nodes without sacrificing quality.

## Installation

```bash
pip install llamaindex-tokenpak
```

## Quick Start

```python
from llamaindex_tokenpak import TokenPakSynthesizer, TokenPakQueryEngine

# Create a TokenPak synthesizer
synthesizer = TokenPakSynthesizer(budget=4000)

# Use in query engine
query_engine = index.as_query_engine(
    synthesizer=synthesizer,
)

# Automatic compression of retrieved nodes
response = query_engine.query("What is the capital of France?")
```

## Features

- **TokenPakSynthesizer**: Compress nodes before synthesis
- **TokenPakQueryEngine**: Query engine wrapper with compression
- **TokenPakIndex**: Index with automatic compression
- **Node converters**: Convert between LlamaIndex and TokenPak formats

## Documentation

See [full documentation](https://tokenpak.dev/integrations/llamaindex).

## License

MIT
