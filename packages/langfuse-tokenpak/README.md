# langfuse-tokenpak

**TokenPak integration for Langfuse** — Visualize context packs in your traces so developers see structured blocks instead of raw prompts.

[![PyPI version](https://img.shields.io/pypi/v/langfuse-tokenpak)](https://pypi.org/project/langfuse-tokenpak/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## What It Does

Instead of seeing a blob of raw text in Langfuse, you see this:

```
TokenPak Pack (3 blocks, 880/8000 tokens)
├── 📋 instructions    [critical]  150 tok
├── 📚 knowledge       [high]      420 tok  [compacted]  src:pinecone
└── 🔍 evidence        [medium]    310 tok
```

Every block shows: type, priority, token count, compression status, and provenance links.

---

## Install

```bash
pip install langfuse-tokenpak
# Optional: install with Langfuse
pip install "langfuse-tokenpak[langfuse]"
# Optional: install with LangChain support
pip install "langfuse-tokenpak[langchain]"
```

---

## Quick Start

### Trace a Pack Directly

```python
from langfuse import Langfuse
from langfuse_tokenpak import TokenPakTracer

langfuse = Langfuse()
tracer = TokenPakTracer(langfuse)

pack = TokenPak(budget=8000)
pack.add_instructions("You are a helpful assistant.")
pack.add_knowledge("api_docs", "... documentation ...")
pack.add_evidence("search_001", "... search results ...")

with tracer.trace_pack(pack, name="rag_query") as span:
    response = llm.complete(pack.to_prompt())
    tracer.record_output(span, response)
```

**What you'll see in Langfuse:**
- Each block listed with type icon + token count
- Budget utilization percentage
- Which blocks were compacted
- Provenance sources

### Track Compression Savings

```python
raw_tokens = count_tokens(raw_docs)  # before compression

with tracer.trace_pack(pack, name="compressed_rag", raw_tokens=raw_tokens) as span:
    response = llm.complete(pack.to_prompt())
    tracer.record_output(span, response)
```

Langfuse metadata will include:
```json
{
  "raw_tokens": 5000,
  "tokens_saved": 2600,
  "compression_ratio": 0.48
}
```

### Session Analytics

```python
analytics = tracer.get_analytics()
print(analytics)
# {
#   "pack_count": 12,
#   "total_tokens_before": 45000,
#   "total_tokens_after": 23400,
#   "savings_percent": 48.0,
#   "type_distribution": {
#     "knowledge": {"tokens": 8200, "count": 12, "percent": 35.0},
#     "evidence":  {"tokens": 6560, "count": 12, "percent": 28.0},
#     ...
#   },
#   "top_blocks": [...]
# }
```

---

## LangChain Integration

```python
from langfuse.callback import CallbackHandler
from langfuse_tokenpak import TokenPakLangChainCallback

callback = TokenPakLangChainCallback(
    langfuse_handler=CallbackHandler(),
    trace_blocks=True,
    trace_compression=True,
)

# Notify the callback when a pack is compiled
callback.on_tokenpak_pack(pack)

# Then use with any LangChain chain
result = chain.invoke(
    {"question": "What is RAG?"},
    config={"callbacks": [callback]},
)
```

---

## LlamaIndex Integration

```python
from llama_index.core.callbacks import CallbackManager
from langfuse_tokenpak import TokenPakLlamaIndexCallback

cb = TokenPakLlamaIndexCallback(langfuse_client)
Settings.callback_manager = CallbackManager([cb])

# In your query engine, pass the pack in the event payload:
callback_manager.on_event_start(
    "query",
    payload={"tokenpak_pack": pack},
    event_id="evt_001"
)
```

---

## Generic Python Callback

```python
from langfuse_tokenpak import TokenPakCallbackHandler

handler = TokenPakCallbackHandler(langfuse_client)

# Call when a pack is compiled
handler.on_tokenpak_compile(pack, compiled_result)
```

---

## Configuration Options

### TokenPakTracer

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trace_blocks` | `True` | Include per-block breakdown in metadata |
| `trace_compression` | `True` | Include compression stats |
| `trace_ascii_summary` | `False` | Add ASCII block diagram to trace input |
| `analytics` | auto | Shared `TokenPakAnalytics` instance |

### TokenPakLangChainCallback

| Parameter | Default | Description |
|-----------|---------|-------------|
| `langfuse_handler` | `None` | Langfuse `CallbackHandler` instance |
| `trace_blocks` | `True` | Include block breakdown |
| `trace_compression` | `True` | Include compression stats |

---

## Visualization

```python
from langfuse_tokenpak import ascii_block_summary, blocks_to_metadata

# ASCII diagram for logging
print(ascii_block_summary(pack.blocks, budget=8000))

# Structured dict for custom trace metadata
meta = blocks_to_metadata(pack.blocks, budget=8000)
```

---

## Graceful Degradation

If Langfuse is not installed or unavailable, all tracing operations silently no-op. Your LLM pipeline keeps running — you just won't see traces until Langfuse is reachable again.

---

## Links

- [TokenPak Docs](https://tokenpak.dev)
- [Langfuse Docs](https://langfuse.com/docs)
- [Integration Guide](https://tokenpak.dev/integrations/langfuse)
- [PyPI](https://pypi.org/project/langfuse-tokenpak/)
