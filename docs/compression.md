# Compression: How It Works

TokenPak intercepts LLM requests on your machine and applies a multi-stage compression pipeline before forwarding them to the provider. The result is semantically equivalent content in fewer tokens.

---

## When Compression Runs

Compression only activates when the request exceeds a token threshold (default: **4,500 tokens**). Requests below the threshold are forwarded unchanged — no overhead.

```
Request received
 │
 ├── input_tokens < threshold → passthrough (0ms overhead)
 │
 └── input_tokens ≥ threshold → compression pipeline
```

Adjust the threshold:

```bash
TOKENPAK_COMPACT_THRESHOLD_TOKENS=2000 tokenpak serve
```

---

## The Pipeline

### Stage 1: Dedup

Scans the message history for duplicate or near-duplicate turns. If the same content appears multiple times (common when context is repeatedly injected), duplicates after the first occurrence are removed.

```python
# Before
messages = [
 {"role": "user", "content": "Here is the code:\n<500 lines>"},
 {"role": "assistant", "content": "I'll review it."},
 {"role": "user", "content": "Here is the code:\n<500 lines>"}, # ← duplicate
 {"role": "user", "content": "Now fix line 42."},
]

# After dedup
messages = [
 {"role": "user", "content": "Here is the code:\n<500 lines>"},
 {"role": "assistant", "content": "I'll review it."},
 {"role": "user", "content": "Now fix line 42."},
]
```

---

### Stage 2: Segmentize

The segmentizer classifies message content into typed blocks:

| Segment type | Content | Compression strategy |
|---|---|---|
| `code` | Fenced code blocks | Signature extraction |
| `markdown` | Headers, lists, prose | Sentence filtering |
| `json` | JSON objects/arrays | Schema + sampling |
| `tool_call` | Tool use / function calls | Keep as-is |
| `tool_result` | Tool outputs | Truncation |
| `system` | System prompt | Recipe-based |
| `text` | Plain prose | Token filtering |

Each segment carries metadata: estimated token count, language (for code), and priority score.

---

### Stage 3: Directives

Directives are declarative compression instructions attached to a recipe. Each directive targets a segment type and describes what to do.

Example directive (`recipes/oss/code-review.yaml`):

```yaml
directives:
 - type: code
 action: signature_only # keep function signatures, strip bodies
 language: [python, js, ts]
 preserve_docstrings: true

 - type: markdown
 action: keep_headers # strip body text, keep heading structure
 max_depth: 3

 - type: text
 action: filter_tokens
 ratio: 0.6 # keep top 60% by importance score
```

Built-in recipes live in `recipes/oss/`. Pro recipes add more aggressive options.

---

### Result

After the pipeline, the `PipelineResult` object contains:

```python
@dataclass
class PipelineResult:
 messages: List[Dict] # compressed messages (same format, fewer tokens)
 segments: List[Segment] # per-segment metadata
 tokens_raw: int # tokens before compression
 tokens_after: int # tokens after compression
 duration_ms: float # pipeline wall time
 stages_run: List[str] # which stages ran

 @property
 def savings_pct(self) -> float: ...
```

---

## Compression Modes

| Mode | `TOKENPAK_MODE` | Behavior |
|---|---|---|
| **Hybrid** (default) | `hybrid` | Compresses when tokens > threshold; skips if below |
| **Strict** | `strict` | Always compresses, no threshold check |
| **Aggressive** | `aggressive` | Maximum compression; accepts some quality reduction |

---

## Engines

### Heuristic engine (default)

Rule-based compression. Runs in <5ms, zero ML dependencies. Handles:

- Regex-based whitespace normalization
- Comment stripping (configurable per language)
- Boilerplate removal (common patterns: `# type: ignore`, `pylint: disable=...`)
- Markdown flattening

### LLMLingua engine (optional, Pro/advanced)

ML-powered token-level compression using the [LLMLingua-2](https://github.com/microsoft/LLMLingua) model. Achieves 2–20x compression with <5% quality loss (per Microsoft benchmarks).

Install:

```bash
pip install tokenpak[compression]
```

LLMLingua activates automatically when installed. It runs locally — no API calls.

---

## Custom Hooks

Add your own compression logic via the pipeline hook API:

```python
from tokenpak.agent.compression.pipeline import CompressionPipeline

def my_hook(messages):
 # Remove messages older than 10 turns
 return messages[-10:]

pipeline = CompressionPipeline()
pipeline.add_hook(my_hook)
result = pipeline.run(messages)
```

Hooks run after the standard stages in insertion order.

---

## Recipe Development

Recipes are YAML files in `recipes/oss/` that define directives for a specific use case.

Minimal recipe:

```yaml
# recipes/oss/my-recipe.yaml
name: my-recipe
version: "1.0"
description: Custom compression for my workflow

directives:
 - type: text
 action: filter_tokens
 ratio: 0.7

 - type: code
 action: signature_only
 language: [python]
```

Apply a recipe:

```bash
tokenpak template use my-recipe
```

See [Recipe Development](guides/recipes.md) for the full directive schema reference.

---

## Dry Run

Preview what compression would do without actually sending the request:

```bash
tokenpak compress myfile.txt
```

Output:

```
Input: 12,840 tokens
Output: 6,918 tokens
Saved: 5,922 tokens (46.1%)
Time: 8.4ms

Stages: dedup (0 removed) → segmentize (14 blocks) → directives (applied)
```

---

## Performance

| Optimization | Speedup |
|---|---|
| LRU token count cache | 25x faster repeated counting |
| Pre-compiled regex | 30% faster processing |
| Batch SQLite WAL writes | 60% faster telemetry |

Compression runs in the request path. On typical payloads it adds **10–50ms**, which is negligible compared to LLM latency (500ms–5s).
