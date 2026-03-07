# TokenPak Public API Reference

**Version:** 0.1.0  
**Author:** Kevin Yang  
**License:** MIT

TokenPak is a deterministic compression and telemetry package for multi-agent AI workflows. This document describes the full public API surface.

---

## Quick Start

```python
from tokenpak import TelemetryCollector, CacheManager, CompressionEngine, Budgeter

# Collect telemetry
collector = TelemetryCollector()

# Manage cache
cache = CacheManager()

# Compress content
engine = CompressionEngine()  # Abstract base; use HeuristicEngine for concrete implementation

# Budget tokens
budgeter = Budgeter()
```

---

## Top-Level Imports

All public classes are importable directly from `tokenpak`:

```python
from tokenpak import (
    TelemetryCollector,
    CompletionTracker,
    CacheManager,
    CompressionEngine,
    HeuristicEngine,
    get_engine,
    Block,
    BlockRegistry,
    Budgeter,
    BudgetBlock,
    main,
    # Sub-packages
    connectors,
    agent,
    proxy,
)
```

---

## Classes

### `TelemetryCollector`

**Import:** `from tokenpak import TelemetryCollector`  
**Source:** `tokenpak.telemetry.collector`

Monitors file system paths and collects telemetry events — cache hits, token counts, cost events.

```python
from tokenpak import TelemetryCollector

collector = TelemetryCollector()
# Watch a directory for events
collector.start()
```

---

### `CompletionTracker`

**Import:** `from tokenpak import CompletionTracker`  
**Source:** `tokenpak.agent.telemetry.cost_tracker.CostTracker` (aliased)

Tracks per-completion cost, model usage, and latency. Accumulates stats across multiple calls.

```python
from tokenpak import CompletionTracker

tracker = CompletionTracker()
tracker.record(model="claude-3-5-sonnet", tokens_in=1200, tokens_out=300, cost_usd=0.015)
stats = tracker.summary()
```

---

### `CacheManager`

**Import:** `from tokenpak import CacheManager`  
**Source:** `tokenpak.telemetry.cache.CacheStore` (aliased)

Manages in-process cache storage with hit-rate tracking. Used by the proxy pipeline.

```python
from tokenpak import CacheManager

cache = CacheManager()
cache.set("key", {"response": "data"})
hit = cache.get("key")
```

---

### `CompressionEngine`

**Import:** `from tokenpak import CompressionEngine`  
**Source:** `tokenpak.engines.base.CompactionEngine` (aliased)

Abstract base class for all compression/compaction strategies. Subclass to implement custom engines.

```python
from tokenpak import CompressionEngine, HeuristicEngine

# Use the concrete heuristic engine
engine = HeuristicEngine()
compressed = engine.compress(content)
```

---

### `HeuristicEngine`

**Import:** `from tokenpak import HeuristicEngine`  
**Source:** `tokenpak.engines.heuristic`

Default production compression engine. Rule-based, fast, no external dependencies.

```python
from tokenpak import HeuristicEngine

engine = HeuristicEngine()
result = engine.compress(text, max_tokens=2000)
```

---

### `get_engine(name)`

**Import:** `from tokenpak import get_engine`  
**Source:** `tokenpak.engines`

Factory function. Returns a compression engine by name.

| Name          | Engine           | Notes                      |
|---------------|------------------|----------------------------|
| `"heuristic"` | HeuristicEngine  | Default, always available  |
| `"fast"`      | HeuristicEngine  | Alias for heuristic        |
| `"balanced"`  | LLMLinguaEngine  | Requires LLMLingua install |
| `"llmlingua"` | LLMLinguaEngine  | Requires LLMLingua install |

```python
from tokenpak import get_engine

engine = get_engine("heuristic")
compressed = engine.compress(content)
```

---

### `Block`

**Import:** `from tokenpak import Block`  
**Source:** `tokenpak.registry`

Dataclass representing a processed content block with compression metadata.

```python
from tokenpak import Block

block = Block(
    path="docs/README.md",
    content_hash="abc123",
    version=1,
    file_type="md",
    raw_tokens=1500,
    compressed_tokens=450,
    compressed_content="...",
    quality_score=0.95,
    importance=7.5,
)
```

**Fields:**
| Field               | Type    | Description                        |
|---------------------|---------|------------------------------------|
| `path`              | str     | Source file path                   |
| `content_hash`      | str     | SHA-256 of original content        |
| `version`           | int     | Block version number               |
| `file_type`         | str     | File type (md, py, json, etc.)     |
| `raw_tokens`        | int     | Token count before compression     |
| `compressed_tokens` | int     | Token count after compression      |
| `compressed_content`| str     | Compressed content string          |
| `quality_score`     | float   | Compression quality (0.0–1.0)      |
| `importance`        | float   | Relevance score (0.0–10.0)         |
| `processed_at`      | float   | Unix timestamp                     |
| `slice_id`          | str     | Optional slice identifier          |
| `provenance`        | object  | Optional source provenance info    |

---

### `BlockRegistry`

**Import:** `from tokenpak import BlockRegistry`  
**Source:** `tokenpak.registry`

SQLite-backed content registry with search, versioning, and stats.

```python
from tokenpak import BlockRegistry, Block

registry = BlockRegistry("tokenpak.db")
registry.add_block(block)

# Search
results = registry.search("token compression", top_k=5)

# Stats
stats = registry.get_stats()
# {'total_files': 42, 'total_tokens': 85000, ...}

registry.close()
```

---

### `Budgeter`

**Import:** `from tokenpak import Budgeter`  
**Source:** `tokenpak.budgeter`

Enforces token budgets across context buckets (state, recent, evidence, tools). Trims lower-priority content first.

```python
from tokenpak import Budgeter

budgeter = Budgeter()  # Loads budget_config.yaml if available

components = {
    'state':    {'text': state_json, 'priority': 'critical'},
    'recent':   {'text': recent_turns, 'priority': 'high'},
    'evidence': {'items': evidence_list, 'priority': 'medium'},
    'tools':    {'text': tool_schemas, 'priority': 'variable'},
}
trimmed = budgeter.allocate(components)
```

**Default budget allocations:**
| Bucket    | Range   | Priority  |
|-----------|---------|-----------|
| STATE_JSON | 8–15%  | critical  |
| RECENT    | 10–20%  | high      |
| EVIDENCE  | 50–70%  | medium    |
| TOOLS     | 0–25%   | variable  |

---

### `BudgetBlock`

**Import:** `from tokenpak import BudgetBlock`  
**Source:** `tokenpak.budget`

Lightweight block reference used for quadratic token allocation.

```python
from tokenpak import BudgetBlock
from tokenpak.budget import quadratic_allocate

blocks = [BudgetBlock(ref="docs/a.md#v1"), BudgetBlock(ref="docs/b.md#v1")]
allocation = quadratic_allocate(blocks, total_tokens=4000)
```

---

### `main()`

**Import:** `from tokenpak import main`  
**Source:** `tokenpak.cli`

CLI entry point. Equivalent to running `tokenpak` from the command line.

```python
from tokenpak import main

main()  # Runs the CLI
```

**CLI usage:**
```bash
tokenpak --help
tokenpak index <path>
tokenpak search <query>
tokenpak stats
tokenpak serve
tokenpak cost
tokenpak budget
```

---

## Sub-Packages

### `tokenpak.telemetry`

Canonical telemetry types, adapters, and collector.

```python
from tokenpak.telemetry import (
    TelemetryCollector,
    CompletionTracker,
    CacheManager,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    UsageSource,
    Confidence,
    AdapterRegistry,
)
```

---

### `tokenpak.engines`

Compression/compaction engines.

```python
from tokenpak.engines import CompactionEngine, HeuristicEngine, get_engine
```

---

### `tokenpak.registry`

Block storage and retrieval.

```python
from tokenpak.registry import Block, BlockRegistry
```

---

### `tokenpak.connectors`

Source connectors (file system, GitHub, etc.)

```python
from tokenpak import connectors
# or
from tokenpak.connectors import ...
```

---

### `tokenpak.agent`

Agent orchestration and vault integration.

```python
from tokenpak import agent
```

---

### `tokenpak.proxy`

Proxy utilities and credential passthrough.

```python
from tokenpak import proxy
from tokenpak.proxy import CredentialPassthrough
```

---

## Version History

| Version | Notes                                      |
|---------|--------------------------------------------|
| 0.1.0   | Initial public API surface — all imports wired |

---

## Integration Example

```python
"""Full integration example: index a directory, search, budget, compress."""
from tokenpak import (
    TelemetryCollector,
    BlockRegistry,
    Block,
    HeuristicEngine,
    Budgeter,
    get_engine,
)
from tokenpak.walker import walk_directory
from tokenpak.processors import get_processor
from tokenpak.tokens import count_tokens
import hashlib

# Set up
registry = BlockRegistry("my_project.db")
engine = get_engine("heuristic")
collector = TelemetryCollector()
budgeter = Budgeter()

# Index a directory
for path, ftype, _ in walk_directory("./docs"):
    content = open(path).read()
    processor = get_processor(ftype)
    if not processor:
        continue
    compressed = processor.process(content, path)
    block = Block(
        path=path,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        file_type=ftype,
        raw_tokens=count_tokens(content),
        compressed_tokens=count_tokens(compressed),
        compressed_content=compressed,
    )
    registry.add_block(block)

# Search and retrieve
results = registry.search("token compression", top_k=5)
print(f"Found {len(results)} results")

registry.close()
```
