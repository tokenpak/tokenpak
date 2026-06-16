# tokenpak-local

TokenPak integration for local LLMs — **Ollama**, **LM Studio**, and any **OpenAI-compatible** endpoint.

Provides automatic context compression sized for the target model's context window, making TokenPak especially valuable where context is limited.

## Why Local LLMs Need This

Local models often have smaller context windows. TokenPak compression makes the difference between "fits" and "doesn't fit":

```
Without TokenPak: 6000 tokens → ❌ Exceeds 4K phi3 limit
With TokenPak:    6000 → 2400 tokens → ✅ Fits in 4K
```

And even with large-context models, compression = faster inference + less RAM.

## Installation

```bash
# Ollama
pip install tokenpak-local[ollama]

# LM Studio / OpenAI-compatible
pip install tokenpak-local[openai]

# Both
pip install tokenpak-local[all]
```

## Quick Start: Ollama

```python
from tokenpak_local import TokenPakOllama, TokenPak, Block

# Build a TokenPak pack
pack = TokenPak()
pack.instructions = "Answer based on the context below."
pack.add(Block(type="evidence", content="The Eiffel Tower is located in Paris, France."))
pack.add(Block(type="evidence", content="It was built between 1887 and 1889."))

# Chat — budget auto-set from llama3's 8192 context window
client = TokenPakOllama()
response = client.chat(model="llama3", tokenpak=pack)
print(response["message"]["content"])
```

### Streaming

```python
for chunk in client.chat(model="llama3", tokenpak=pack, stream=True):
    print(chunk["message"]["content"], end="", flush=True)
```

### With a User Message

```python
response = client.chat(
    model="llama3",
    tokenpak=pack,
    messages=[{"role": "user", "content": "Where is the Eiffel Tower?"}]
)
```

### Using generate()

```python
response = client.generate(
    model="llama3",
    tokenpak=pack,
    prompt="Explain the above context in one sentence."
)
print(response["response"])
```

## Quick Start: LM Studio

```python
from tokenpak_local import TokenPakLMStudio, TokenPak, Block

client = TokenPakLMStudio()  # connects to http://localhost:1234/v1

pack = TokenPak(instructions="Answer concisely.")
pack.add(Block(type="evidence", content="Context goes here..."))

response = client.complete(
    model="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
    tokenpak=pack,
    user_message="What does the context say?"
)
print(response.choices[0].message.content)
```

### List Available Models

```python
models = client.list_models()
print(models)  # ["meta-llama-3-8b-instruct", ...]
```

## Quick Start: Any OpenAI-Compatible Endpoint

```python
from tokenpak_local import TokenPakOpenAICompat, TokenPak, Block

# Works with LocalAI, llama.cpp server, vLLM, TabbyAPI, Ollama OpenAI mode, etc.
client = TokenPakOpenAICompat(
    base_url="http://localhost:8080/v1",  # your endpoint
    api_key="any-key",
    context_length=8192,  # override if auto-detection isn't available
)

pack = TokenPak(instructions="You are a helpful assistant.")
response = client.complete(
    model="my-model",
    tokenpak=pack,
    user_message="Hello!"
)
```

## Auto-Budget

The core feature: `auto_budget()` computes a safe input token budget from the model's known context window.

```python
from tokenpak_local import auto_budget, get_context_length

# Get context length for any known model
get_context_length("llama3")       # → 8192
get_context_length("phi3")         # → 4096
get_context_length("llama3.1:8b") # → 131072
get_context_length("mistral")      # → 32768

# Compute input budget (default: 75% of context window)
auto_budget("llama3")       # → 6144  (75% of 8192)
auto_budget("phi3")         # → 3072  (75% of 4096)
auto_budget("llama3.1:8b") # → 98304 (75% of 131072)

# Custom output fraction
auto_budget("llama3", output_fraction=0.5)  # → 4096 (50/50 split)

# Override context length
auto_budget("my-custom-model", context_length=16384)  # → 12288
```

### Known Models

| Model Family | Context Length |
|---|---|
| llama3 | 8,192 |
| llama3.1 / 3.2 / 3.3 | 131,072 |
| llama2 | 4,096 |
| mistral / mistral-nemo | 32,768 / 131,072 |
| phi3 | 4,096 |
| phi3.5 | 131,072 |
| phi4 | 16,384 |
| gemma2 | 8,192 |
| qwen2 / qwen2.5 | 32,768–131,072 |
| deepseek-r1 | 65,536 |
| command-r | 131,072 |
| codellama | 16,384 |

Unknown models default to 4,096. Extend `MODEL_CONTEXT_LENGTHS` for custom models.

## Utilities

### blocks_from_texts

Convert retrieved documents to TokenPak Blocks:

```python
from tokenpak_local import blocks_from_texts

docs = ["Document 1 content...", "Document 2 content..."]
blocks = blocks_from_texts(docs, block_type="evidence")
```

### pack_from_blocks

Build a TokenPak from a block list:

```python
from tokenpak_local import pack_from_blocks, auto_budget

budget = auto_budget("llama3")
pack = pack_from_blocks(blocks, instructions="Answer the question.", budget=budget)
```

## Full Local RAG Pipeline

```python
import ollama
from tokenpak_local import (
    TokenPakOllama,
    blocks_from_texts,
    pack_from_blocks,
    auto_budget,
)

# Embed the query
query = "What is TokenPak?"
embedding_resp = ollama.embeddings(model="nomic-embed-text", prompt=query)

# Retrieve from vector DB (pseudo-code)
results = vector_db.query(embedding_resp["embedding"], top_k=5)
docs = [r.text for r in results]

# Build TokenPak
budget = auto_budget("llama3")  # 6144 tokens (75% of 8192)
blocks = blocks_from_texts(docs, block_type="evidence")
pack = pack_from_blocks(
    blocks,
    instructions="Answer the question based on the evidence below.",
    budget=budget,
)

# Inference
client = TokenPakOllama()
response = client.chat(
    model="llama3",
    tokenpak=pack,
    messages=[{"role": "user", "content": query}]
)
print(response["message"]["content"])
```

## Model Compatibility

Tested with:
- **Ollama**: llama3, llama3.1, mistral, phi3, gemma2, qwen2.5, deepseek-r1
- **LM Studio**: Any GGUF model loaded via the LM Studio server
- **OpenAI-compatible**: LocalAI, llama.cpp, vLLM, TabbyAPI, Ollama (OpenAI mode)

## License

Apache-2.0
