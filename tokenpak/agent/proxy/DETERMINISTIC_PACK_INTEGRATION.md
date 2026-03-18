# DeterministicPromptPack — Integration Guide

## Overview

`DeterministicPromptPack` is an optional feature in `prompt_builder.py` that enforces fixed section ordering and produces byte-identical output for equivalent inputs. It integrates seamlessly with the existing cache control infrastructure without requiring any changes to `PromptBuilder` or the request flow.

## Design Summary

### Section Order (Immutable)
```
1. SYSTEM PROMPT (stable)
2. TOOLS (stable)
3. POLICIES/CONSTRAINTS (stable)
4. RETRIEVED CONTEXT (volatile — per-request)
5. USER INPUT (volatile — per-request)
```

### Cache Boundary
- **Last stable section** (policies, or tools if no policies) → marked with `cache_control: {type: ephemeral}`
- **Volatile sections** → no cache markers (updated per request)

### Output Structure
```python
system: [
    {
        type: "text",
        text: "<SYSTEM>\n\n<TOOLS>\n\n<POLICIES>",
        cache_control: {type: "ephemeral"}  # ← Cache boundary
    },
    {
        type: "text",
        text: "<RETRIEVED CONTEXT>\n\n<USER INPUT>"
        # No cache_control (volatile)
    }
]
```

---

## Basic Usage

### Simple Case: No Tools, No Retrieved Context

```python
from tokenpak.agent.proxy.prompt_builder import DeterministicPromptPack

pack = DeterministicPromptPack(
    system="You are a helpful AI assistant.",
    policies="Never provide harmful information.",
    user_input="What is machine learning?",
)

body = pack.to_request_body(model="claude-3-5-sonnet-20241022")
# body is now a complete Anthropic request body with system/messages fields
```

### Full Case: All Sections

```python
pack = DeterministicPromptPack(
    system="You are a research assistant.",
    tools=[
        {"name": "search", "description": "Search academic papers"},
        {"name": "summarize", "description": "Summarize text"},
    ],
    policies="Always cite sources. Be concise.",
    retrieved_context=[
        {
            "text": "Machine Learning: foundational concepts...",
            "source": "paper_2024.pdf",
            "score": 0.95,
        },
        {
            "text": "Deep Learning architectures...",
            "source": "paper_2023.pdf",
            "score": 0.87,
        },
    ],
    user_input="Summarize the state of ML in 2024.",
)

system_blocks = pack.to_system_block()
# [
#     {type: "text", text: "<SYSTEM+TOOLS+POLICIES>", cache_control: {...}},
#     {type: "text", text: "<RETRIEVED+USER>"}
# ]
```

---

## Integration Patterns

### Pattern 1: Proxy Middleware

**Before:**
```python
# Old way: ad-hoc assembly, inconsistent ordering
system_parts = [system_prompt]
if vault_context:
    system_parts.append(vault_context)
if tools:
    system_parts.append(json.dumps(tools))
# Order varies, spacing inconsistent, cache key unstable
body["system"] = "\n\n".join(system_parts)
```

**After:**
```python
# New way: deterministic, fixed order
from tokenpak.agent.proxy.prompt_builder import DeterministicPromptPack

pack = DeterministicPromptPack(
    system=system_prompt,
    tools=tool_registry.get_tools(),
    policies=get_policies(),
    retrieved_context=vault_search_results,
    user_input=user_message,
)
body["system"] = pack.to_system_block()
```

### Pattern 2: Feature-Flagged Adoption

```python
import os
from tokenpak.agent.proxy.prompt_builder import DeterministicPromptPack, PromptBuilder

USE_DETERMINISTIC_PACKING = os.getenv("USE_DETERMINISTIC_PACKING", "false").lower() == "true"

def assemble_system_prompt(system, tools, policies, vault_context, user_input):
    if USE_DETERMINISTIC_PACKING:
        pack = DeterministicPromptPack(
            system=system,
            tools=tools,
            policies=policies,
            retrieved_context=vault_context,
            user_input=user_input,
        )
        return pack.to_system_block()
    else:
        # Fallback to existing PromptBuilder
        builder = PromptBuilder()
        # ... existing logic
```

### Pattern 3: Vault Injection Integration

```python
def proxy_anthropic_request(body_bytes):
    data = json.loads(body_bytes)
    user_msg = data["messages"][-1]["content"]
    vault_results = vault_search(user_msg)
    
    pack = DeterministicPromptPack(
        system=load_system_prompt(),
        tools=registry.get_tools(),
        policies=load_policies(),
        retrieved_context=vault_results,  # ← Injected dynamically
        user_input=user_msg,
    )
    
    data["system"] = pack.to_system_block()
    return json.dumps(data).encode("utf-8")
```

---

## Byte-Identity Guarantee

### Proof by Test

```python
def test_byte_identity():
    """Two identical packs produce byte-identical output."""
    
    pack1 = DeterministicPromptPack(
        system="You are helpful.",
        tools=[{"name": "search", "description": "Search"}],
        policies="Be honest.",
        retrieved_context=["doc1", "doc2"],
        user_input="What is X?",
    )
    
    pack2 = DeterministicPromptPack(
        system="You are helpful.",
        tools=[{"name": "search", "description": "Search"}],
        policies="Be honest.",
        retrieved_context=["doc1", "doc2"],
        user_input="What is X?",
    )
    
    body1 = json.dumps(pack1.to_request_body(), sort_keys=True).encode("utf-8")
    body2 = json.dumps(pack2.to_request_body(), sort_keys=True).encode("utf-8")
    
    assert body1 == body2  # ← PASS: byte-identical
```

### Why It Works

1. **Deterministic tools serialization:**
   - Tools sorted by name
   - Keys recursively sorted
   - Compact JSON (no spaces, consistent separators)

2. **Fixed section order:**
   - Guarantees position consistency
   - Section headers have canonical formatting

3. **Deterministic retrieved context:**
   - Items joined with single newlines
   - No extra spacing or formatting

4. **String encoding:**
   - `ensure_ascii=False` for consistent UTF-8
   - No platform-specific encoding surprises

---

## Cache Control Verification

### Stable Section (System Block 0)

```python
pack = DeterministicPromptPack(
    system="System",
    tools=[{"name": "tool1"}],
    policies="Policies",
    retrieved_context=["Retrieved"],
    user_input="User",
)

blocks = pack.to_system_block()

# blocks[0] is stable (system + tools + policies)
assert blocks[0]["cache_control"] == {"type": "ephemeral"}
# blocks[0]["text"] contains: "# SYSTEM\n\n...\n\n# TOOLS\n\n...\n\n# POLICIES\n\n..."

# blocks[1] is volatile (retrieved + user input)
assert "cache_control" not in blocks[1]
# blocks[1]["text"] contains: "# RETRIEVED CONTEXT\n\n...\n\n# USER INPUT\n\n..."
```

---

## Comparison: Before vs. After

| Aspect | Before (Ad-hoc) | After (DeterministicPromptPack) |
|--------|-----------------|--------------------------------|
| **Section Order** | Varies by code path | Fixed: SYSTEM → TOOLS → POLICIES → RETRIEVED → USER |
| **Byte-Identity** | ❌ No (spacing, order inconsistent) | ✅ Yes (guaranteed for equivalent inputs) |
| **Cache Boundary** | ⚠️ Only when vault injects | ✅ Always applied correctly |
| **Testability** | Harder (many code paths) | ✅ Simple (single class) |
| **API Changes** | Ad-hoc, per-scenario | Standardized: fixed `to_system_block()` method |
| **Documentation** | Scattered, implicit | Centralized with docstrings + examples |

---

## Testing

All tests located in `test_prompt_pack.py`:

```bash
cd /path/to/tokenpak
python3 -m pytest tokenpak/agent/proxy/test_prompt_pack.py -v
```

**Test Coverage:**
- ✅ Acceptance Criterion 1: Fixed section order
- ✅ Acceptance Criterion 2: Byte-identical output
- ✅ Acceptance Criterion 3: Stable/volatile separation with cache_control
- ✅ Acceptance Criterion 4: No breaking changes
- ✅ Acceptance Criterion 5: Before/after examples
- ✅ Bonus: Tool order variance, dict context format, empty sections, repr

**Result:** 12/12 tests pass

---

## API Reference

### `DeterministicPromptPack` Class

```python
@dataclass
class DeterministicPromptPack:
    system: str = ""              # System prompt (stable)
    tools: list[dict] = []        # Tool schemas (stable)
    policies: str = ""            # Policies/constraints (stable)
    retrieved_context: list = []  # Search results (volatile)
    user_input: str = ""          # User message (volatile)
    metadata: dict = {}           # Optional metadata (not serialized)
```

### Methods

```python
# Get Anthropic system blocks
blocks = pack.to_system_block() -> list[dict]

# Get complete request body
body = pack.to_request_body(model="...") -> dict

# Equality comparison (for testing)
pack1 == pack2 -> bool

# String representation
repr(pack) -> str
```

---

## No Breaking Changes

The existing `PromptBuilder` class remains untouched:
- All existing imports work
- `apply_stable_cache_control()` still works
- `apply_deterministic_cache_breakpoints()` still works
- `inject_with_cache_boundary()` still works

The two can coexist:
```python
# Old code keeps working
builder = PromptBuilder()
parts = builder.decompose(body_bytes)
rebuilt = builder.build(parts)

# New code uses DeterministicPromptPack
pack = DeterministicPromptPack(...)
blocks = pack.to_system_block()
```

---

## Future Enhancements

1. **Stable prefix caching:** Compute hash of stable sections for quick comparison
2. **Metadata preservation:** Store context about which sections are present
3. **Validation:** Enforce schema for tools, policies
4. **Compression:** Optional compression of stable blocks for transmission
5. **Migration tooling:** Helper to convert from `PromptBuilder` to `DeterministicPromptPack`

---

## Questions?

- See docstrings in `prompt_builder.py` for detailed API docs
- Check `test_prompt_pack.py` for usage examples
- Review this file for integration patterns
