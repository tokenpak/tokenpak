# TokenPak Architecture Overview

## What is TokenPak?

TokenPak is a **deterministic token-budget compiler** for large language models. It compresses context, tracks tokens, optimizes costs, and provides adapters for multiple LLM providers — enabling developers to build multi-agent AI systems with predictable token budgets and costs.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Applications / Integrations              │
│  (adapters: LiteLLM, CrewAI, LangChain, LLamaIndex, etc.)  │
└─────────────────────────────────────────────────────────────┘
                           ↓ ↓ ↓
┌─────────────────────────────────────────────────────────────┐
│                     TokenPak Proxy & CLI                    │
│  (serve.py, cli.py, proxy.py — handle requests/responses)  │
└─────────────────────────────────────────────────────────────┘
                           ↓ ↓ ↓
┌─────────────────────────────────────────────────────────────┐
│                    Core Processing Layer                    │
│  Compression │ Token Counting │ Budget Allocation │ Routing │
└─────────────────────────────────────────────────────────────┘
                           ↓ ↓ ↓
┌─────────────────────────────────────────────────────────────┐
│              Telemetry & Cost Tracking Layer                │
│  (telemetry/, cost_router.py — metrics, costs, analytics)  │
└─────────────────────────────────────────────────────────────┘
                           ↓ ↓ ↓
┌─────────────────────────────────────────────────────────────┐
│            LLM Provider APIs (OpenAI, Anthropic, Google)    │
└─────────────────────────────────────────────────────────────┘
```

## Package Structure

### Core Modules (`tokenpak/`)

| Module | Purpose |
|--------|---------|
| **core.py** | Fundamental block, registry, and context compilation |
| **budgeter.py** | Token budget allocation and trimming logic |
| **tokens.py** | Token counting for different models (cached) |
| **compaction/** | Compression algorithms (heuristic, LLMLingua, etc.) |
| **calibrator.py** | Token counting calibration and accuracy tuning |
| **registry.py** | Block registry for storing/retrieving compiled context |
| **cli.py** | Command-line interface for manual compression/testing |

### Adapter Layer (`tokenpak/integrations/` + `packages/`)

| Package | Adapters |
|---------|----------|
| **integrations/litellm/** | LiteLLM proxy (OpenAI, Anthropic, Google, etc.) |
| **packages/tokenpak-local/** | Local OpenAI-compatible SDK wrapper |
| **packages/langchain-tokenpak/** | LangChain integration |
| **packages/llamaindex-tokenpak/** | LLamaIndex integration |
| **packages/crewai-tokenpak/** | CrewAI multi-agent framework |
| **packages/autogen-tokenpak/** | Microsoft AutoGen integration |

### Telemetry & Analytics (`tokenpak/telemetry/`)

| Component | Purpose |
|-----------|---------|
| **api.py** | Telemetry API endpoints (FastAPI) |
| **storage_events.py** | Event logging and persistence |
| **adapters/** | Provider-specific metrics (OpenAI, Anthropic, Gemini) |
| **dashboard/** | Web UI for cost analysis and trends |
| **cost_router.py** | Intelligent provider selection by cost |

### Additional Components

| Module | Purpose |
|--------|---------|
| **agent/** | Multi-agent coordination (handoff, routing, query planning) |
| **validation/** | Schema validation for blocks and context |
| **cache/** | Response caching and retrieval |
| **connectors/** | External data sources (GitHub, databases) |
| **proxy.py** | HTTP/WebSocket proxy for LLM requests |
| **cli.py** | CLI for compression, testing, and setup |

## Core Components Explained

### 1. Block Registry

A **Block** is a named piece of context (evidence, instructions, chat history). The **BlockRegistry** stores and retrieves blocks using SQLite.

```python
block = Block(content="...", type="evidence", quality=0.9)
registry = BlockRegistry("path/to/db")
registry.store(block)
```

### 2. Token Budgeter

Allocates tokens across context sections (state, recent, evidence, tools) and trims lower-priority content when over budget.

```python
budgeter = Budgeter(total_tokens=4096)
trimmed = budgeter.allocate({
    'state': {'text': '...', 'priority': 'critical'},
    'recent': {'text': '...', 'priority': 'high'},
    'evidence': {'items': [...], 'priority': 'medium'},
})
```

### 3. Compression Engines

Multiple compression strategies available:
- **Heuristic**: Fast pattern-based summarization
- **LLMLingua**: LLM-guided compression (slow, high accuracy)
- **Recursive**: Token-aware recursive chunking

```python
engine = get_engine("heuristic")  # or "llmlinga", "recursive"
compressed = engine.compress(context, max_tokens=2048)
```

### 4. Telemetry Adapters

Track costs and metrics per provider:
- **OpenAI**: GPT-4, GPT-3.5-turbo, o1 pricing and token counting
- **Anthropic**: Claude 3 variants, cache tokens, input/output tracking
- **Google**: Gemini models, safety ratings, multi-modal handling

### 5. Multi-Agent Agent (tokenpak/agent/)

- **Handoff**: Context passing between agents
- **Query Planner**: Break complex queries into sub-tasks
- **Router**: Select best provider/model by cost
- **Vault**: Version-controlled knowledge base

## Request Flow (Example: LiteLLM Adapter)

1. **User Request** → LiteLLM adapter receives `messages` + `budget`
2. **Tokenize** → Count tokens in existing messages
3. **Compress** → If over budget, compress context blocks
4. **Route** → Select best provider (cost_router.py)
5. **Call LLM** → Make request to OpenAI/Anthropic/Google
6. **Track** → Log cost, tokens, latency to telemetry
7. **Cache** → Store response in cache for reuse
8. **Return** → Stream response back to user

## Adapters & Integrations

### Built-in Integrations

- **LiteLLM**: Proxy and formatter for 100+ LLM APIs
- **Langchain**: LangChain LLM interface + tools
- **LLamaIndex**: LlamaIndex LLM integration
- **CrewAI**: Multi-agent framework with context compression
- **AutoGen**: Microsoft AutoGen agent coordination
- **Telegram**: Messaging API integration

### Adapter Pattern

All adapters follow the same pattern:
1. Receive user messages + optional TokenPak budget
2. Compress context if needed
3. Call LLM API
4. Track costs + tokens
5. Return response

## Key Interfaces

### Main Classes

- **Block**: Context unit (evidence, instructions, etc.)
- **BlockRegistry**: SQLite-backed block storage
- **Budgeter**: Token allocation and trimming
- **CompressionEngine**: Abstract base for compression algorithms
- **CostRouter**: Provider selection by cost efficiency
- **DeterministicRouter**: Multi-agent routing with determinism
- **TelemetryCollector**: Cost and performance metrics

### Entry Points

```python
from tokenpak import (
    Budgeter,
    Block,
    BlockRegistry,
    CompressionEngine,
    TelemetryCollector,
    get_engine,
    count_tokens,
)

# For adapters
from tokenpak.integrations.litellm import LiteLLMProxy
from tokenpak_local import OpenAICompat
```

## Development Workflow

### Adding a New Adapter

1. Create `packages/yourframework-tokenpak/` directory
2. Implement LLM interface (chat, completion, streaming)
3. Call TokenPak core for token budgeting
4. Log telemetry events
5. Add tests

### Adding a New Compression Algorithm

1. Subclass `CompressionEngine` in `tokenpak/compaction/`
2. Implement `compress(context: str, max_tokens: int) -> str`
3. Add determinism guarantees if needed
4. Register in `tokenpak/engines/__init__.py`

## Quality Metrics

- **Type Safety**: 100% mypy compliance (tokenpak-local, 90%+ core)
- **Test Coverage**: 80%+ across adapters and core
- **Performance**: Sub-100ms token counting, <5s compression
- **Determinism**: All compression is input-deterministic (same input = same output)

---

*Last updated: 2026-03-09*
