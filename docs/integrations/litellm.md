# TokenPak × LiteLLM Integration

LiteLLM is a de-facto LLM gateway used by thousands of teams to route model
calls across providers. This integration lets any LiteLLM user adopt TokenPak
with **a one-line change**.

## Installation

```bash
pip install tokenpak   # LiteLLM is an optional dependency
pip install litellm    # install separately if not already present
```

## Quick Start

### Option 1 — `TokenPakMiddleware` (Recommended)

Drop the middleware into your existing `Router`:

```python
from litellm import Router
from tokenpak.integrations.litellm import TokenPakMiddleware
from tokenpak import BlockRegistry

router = Router(
    model_list=[
        {"model_name": "gpt-4", "litellm_params": {"api_key": "..."}},
        {"model_name": "claude-3", "litellm_params": {"api_key": "..."}},
    ],
    middleware=[TokenPakMiddleware(compaction="balanced", budget=8000)],
)

# Populate a pack
pack = BlockRegistry()
# ... add blocks ...

# One-line change: pass tokenpak= instead of messages=
response = await router.acompletion(model="gpt-4", tokenpak=pack)

# Response includes stats
print(response.tokenpak_stats)
# {"compile_ms": 3.2, "budget": 8000, "compaction": "balanced", "system_tokens": 620}
```

### Option 2 — Standalone Wrapper

Works with plain `litellm.completion()` (no Router needed):

```python
import litellm
from tokenpak.integrations.litellm import TokenPakMiddleware

mw = TokenPakMiddleware(compaction="balanced")

response = litellm.completion(
    **mw.wrap_kwargs(
        model="gpt-4",
        tokenpak=pack,
        messages=[{"role": "user", "content": "Summarize the docs."}],
    )
)
```

### Option 3 — Auto-Detection in Messages

```python
import litellm

response = litellm.completion(
    model="gpt-4",
    messages=[
        {
            "role": "user",
            "content": {
                "type": "tokenpak",   # <-- auto-detected
                "pack": pack_dict,
            },
        }
    ],
)
```

### Option 4 — `/tokenpak` Proxy Endpoint

Add a dedicated endpoint to your Starlette/FastAPI app:

```python
from starlette.applications import Starlette
from starlette.routing import Route
from tokenpak.integrations.litellm import ProxyHandler

handler = ProxyHandler(default_model="gpt-4", budget=8000)

app = Starlette(routes=[
    Route("/tokenpak", handler.handle, methods=["POST"]),
])
```

Call it with curl:

```bash
curl -X POST http://localhost:8000/tokenpak \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "tokenpak": {
      "version": "1.0",
      "blocks": [
        {"ref": "docs/intro", "type": "text", "content": "...", "tokens": 200}
      ],
      "policies": {"compaction": "balanced", "budget": 8000}
    },
    "messages": [
      {"role": "user", "content": "Summarize the docs."}
    ]
  }'
```

## Compaction Strategies

| Strategy      | Description                                    | Use when                          |
|---------------|------------------------------------------------|-----------------------------------|
| `"none"`      | No compaction — raw blocks concatenated        | Small packs, debugging            |
| `"balanced"`  | Heuristic compaction (default)                 | Most production use cases         |
| `"aggressive"`| Hard-truncate to fit budget                    | Very large packs, tight budgets   |

Override per-call:

```python
mw.wrap_kwargs(model="gpt-4", tokenpak=pack, tokenpak_compaction="aggressive", tokenpak_budget=4000)
```

## Middleware API

### `TokenPakMiddleware(compaction, budget, telemetry)`

| Param        | Type   | Default      | Description                              |
|--------------|--------|--------------|------------------------------------------|
| `compaction` | str    | `"balanced"` | Default compaction strategy              |
| `budget`     | int    | `8000`       | Default token budget                     |
| `telemetry`  | bool   | `True`       | Attach `tokenpak_stats` to responses     |

### `TokenPakMiddleware.wrap_kwargs(**kwargs)`

Pre-processes kwargs for `litellm.completion()`. Compiles `tokenpak=` into
`messages=`. Strips internal keys so LiteLLM never sees them.

## Low-Level API

```python
from tokenpak.integrations.litellm import compile_pack, blocks_to_messages

# Convert a BlockRegistry or list of Blocks to messages
messages = compile_pack(pack, budget=8000, compaction="balanced")

# Convert a list of Block objects directly
messages = blocks_to_messages(blocks, budget=4000)
```

## How It Works

1. **Detection** — `parser.parse_tokenpak_request()` detects TokenPak in four ways:
   explicit kwarg, message content type, raw body dict, or wire-format passthrough.
2. **Compilation** — `formatter.compile_pack()` converts blocks to TOKPAK wire
   format, applying the chosen compaction strategy if the pack exceeds budget.
3. **Injection** — The compiled wire format becomes the `system` message; any
   existing messages follow (existing system messages are replaced).
4. **Stats** — Middleware attaches `tokenpak_stats` to the response with
   compile time, budget, compaction strategy, and token counts.

## Contributing to LiteLLM Core

The long-term goal is a PR to [litellm/litellm](https://github.com/BerriAI/litellm)
adding `tokenpak=` as a first-class parameter. This integration serves as the
reference implementation. See `ROADMAP.md` for status.
