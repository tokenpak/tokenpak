# Stable Prefix Content-Address Registry

**Module:** `tokenpak/cache/prefix_registry.py`  
**Public via:** `from tokenpak.cache import StablePrefixRegistry, fingerprint, get_registry`

---

## What it does

Assigns deterministic, content-derived IDs to stable prompt payloads (system prompts, tool definitions, pack schemas — anything that's stable between turns).

Identical content **always** maps to the same ID, regardless of:
- Dict key ordering
- Equivalent JSON whitespace variants

This makes stable block IDs safe to compare, log, and trace across sessions.

---

## ID format

```
spfx-<16-hex-chars>
```

Example: `spfx-3a7f1c8b2e0d94f1`

The ID is the first 16 hex characters of the SHA-256 digest of the canonical form of the payload.

---

## Quick start

```python
from tokenpak.cache import get_registry, fingerprint

# Process-level singleton registry
reg = get_registry()

# Register / look up a stable block
block_id, is_new = reg.get_or_create(system_prompt_payload)
print(block_id)   # "spfx-3a7f1c..."

# Inspect metadata
meta = reg.metadata(block_id)
# {
#   "block_id":   "spfx-3a7f1c...",
#   "first_seen": 1741620000.123,  # Unix timestamp
#   "last_seen":  1741623600.456,
#   "hit_count":  7,
#   "size_bytes": 412
# }
```

---

## Non-breaking integration into proxy/assembler

The registry is read-only from the perspective of the wire format — it doesn't modify the payload. Attach the `block_id` to diagnostics/logging after assembly:

```python
from tokenpak.cache import get_registry

reg = get_registry()

def record_stable_prefix(payload: dict, request_id: str) -> str:
    block_id, is_new = reg.get_or_create(payload)
    logger.debug(
        "[proxy] stable_prefix block_id=%s is_new=%s request_id=%s",
        block_id, is_new, request_id
    )
    return block_id
```

No changes to the outgoing payload. No risk of cache breakage.

---

## API reference

### `fingerprint(payload) -> str`

Returns the stable block ID for `payload`. Pure function, no state.

```python
fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})  # True
```

### `canonicalize(payload) -> bytes`

Returns the canonical byte form of `payload`:
- `bytes` → passthrough
- `str` → UTF-8 encoded
- `dict`/`list` → JSON with sorted keys and no extra whitespace

### `get_registry() -> StablePrefixRegistry`

Returns the process-level singleton. Thread-safe.

### `reset_registry() -> None`

Resets the singleton. Intended for test isolation only.

### `StablePrefixRegistry`

Thread-safe, in-memory registry. Can be instantiated independently for scoped use.

| Method | Description |
|---|---|
| `get_or_create(payload) -> (block_id, is_new)` | Register or look up a block |
| `metadata(block_id) -> dict \| None` | Per-block metadata |
| `all_metadata() -> dict` | Snapshot of all entries |
| `size() -> int` | Number of distinct blocks tracked |
| `summary() -> dict` | High-level summary for logging |
| `clear() -> None` | Wipe all entries |

---

## Inspecting block IDs in logs

When debug logging is enabled (`DEBUG` level on `tokenpak.cache.prefix_registry`), every call emits:

```
[PrefixRegistry] new block_id=spfx-3a7f1c... size=412
[PrefixRegistry] hit block_id=spfx-3a7f1c... hit_count=3
```

To inspect all currently-tracked blocks at runtime:

```python
from tokenpak.cache import get_registry
import json
print(json.dumps(get_registry().all_metadata(), indent=2))
```

---

## Design decisions

| Decision | Rationale |
|---|---|
| SHA-256 + 16 hex chars | 64-bit ID space — negligible collision probability for prompt prefix volumes |
| In-memory only | No I/O latency; persistence not required for correctness |
| Separate from StableCache | Avoids circular dependency; single responsibility |
| No TTL | Block IDs are stable by definition; they don't expire |
| Thread-safe | Proxy runs multi-threaded; all registry ops use a single lock |

---

## Migration notes

- **Existing code is unaffected.** The registry is purely additive.
- Wire format, cache control headers, and request payloads are not modified.
- To adopt: call `get_registry().get_or_create(payload)` at any point where you assemble a stable prompt section. Log or attach the returned `block_id` to your diagnostics dict.
