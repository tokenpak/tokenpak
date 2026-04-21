# TokenPak Semantic Layer — Term-Card Resolver

Deterministic term-card resolver for glossary integration in the proxy's request handling.

## Overview

The semantic layer provides:

- **Deterministic term extraction** from user queries against `term_cards.json` glossary
- **Cache-stable glossary injection** for prompt cache hits (byte-identical repeated runs)
- **Strict runtime policy enforcement** (zero injection by default, top-K caps, short fields only)
- **Safe feature flagging** for gradual rollout without regression risk
- **Ambiguity detection** with deterministic fallback handling

## Architecture

### Module Structure

```
tokenpak/agent/semantic/
├── __init__.py              # Public API exports
├── term_resolver.py         # Core resolver implementation
├── test_term_resolver.py    # Unit tests (19 tests)
├── test_proxy_integration.py # Integration tests (13 tests)
└── README.md                # This file
```

### Core Components

#### TermResolver

Main resolver class that:
- Loads `term_cards.json` glossary
- Extracts matching terms (canonical + aliases)
- Enforces hard caps (top-K, bytes per card)
- Detects and reports ambiguity deterministically
- Formats snippets for system prompt injection

#### TermResolverConfig

Configuration dataclass for controlling resolution behavior:
```python
config = TermResolverConfig(
    top_k=3,                  # Max cards to return (capped at 5)
    max_bytes_per_card=200,   # Truncation limit per snippet
    enabled=True,             # Feature flag
)
```

#### TermResolution

Result object containing:
- `canonical_ids`: List of matched term IDs
- `card_snippets`: Short-form cards ready for injection
- `ambiguous`: Boolean flag if multiple matches detected
- `ambiguity_question`: Deterministic disambiguation question
- `injection_text`: Formatted text ready for system prompt
- `tokens_estimate`: Rough token count for budget planning

## Integration with the proxy

### Feature Flag

Enable term resolution via environment variable:

```bash
export TOKENPAK_TERM_RESOLVER_ENABLED=1
export TOKENPAK_TERM_RESOLVER_TOP_K=3
export TOKENPAK_TERM_RESOLVER_MAX_BYTES=200
```

Default: **disabled** (zero overhead, no behavior change)

### Request Pipeline

1. Extract query signal from user request
2. **[NEW]** Resolve glossary terms from query (if enabled)
3. Compile glossary injection (if terms matched)
4. Search vault index for relevant context
5. Combine glossary + vault injection
6. Apply skeleton extraction to code blocks
7. Inject combined context into system prompt

### Integration Points

**Proxy integration (in `tokenpak/agent/proxy/server.py`):**

```python
# Feature import (safe fallback if unavailable)
try:
    from tokenpak.agent.semantic import TermResolver, TermResolverConfig
    TERM_RESOLVER_AVAILABLE = True
except ImportError:
    TERM_RESOLVER_AVAILABLE = False

# Global initialization (gated by feature flag)
TERM_RESOLVER = None
if TERM_RESOLVER_AVAILABLE and TERM_RESOLVER_ENABLED:
    config = TermResolverConfig(...)
    TERM_RESOLVER = TermResolver(config=config)

# Updated inject_vault_context() to call term resolver
def inject_vault_context(body_bytes, adapter=None):
    # ... extract query ...
    if TERM_RESOLVER is not None:
        resolution = TERM_RESOLVER.resolve_terms(query)
        glossary_injection = resolution.injection_text or ""
        glossary_tokens = resolution.tokens_estimate
        # ... adjust vault budget ...
    # ... combine glossary + vault ...
```

### Health Endpoint

The `/health` endpoint reports term resolver status:

```json
{
  "term_resolver": {
    "enabled": true,
    "available": true,
    "top_k": 3,
    "max_bytes_per_card": 200
  }
}
```

## Runtime Policy (Enforced)

### 1. Zero Injection by Default
Unless matched terms exist in query, **no glossary content injected**.

```python
query = "Tell me about the weather"
result = resolver.resolve_terms(query)
# canonical_ids == []
# injection_text == None
```

### 2. Top-K Hard Caps
- Default: K=3 cards
- Maximum: K=5 (enforced in config)
- Ordered deterministically (by tier, confidence, canonical ID)

```python
config = TermResolverConfig(top_k=10)
# Clamped to min(10, 5) == 5
```

### 3. Per-Card Short Fields
- `meaning` truncated to `max_bytes_per_card` (default 200 bytes)
- No full card dumps; only essential fields injected
- Aliases limited to top 2 per card

### 4. Deterministic Output
- Same query → byte-identical results (cache stable)
- Ordering: tier desc, confidence desc, canonical_id asc
- Ambiguity: single deterministic question, not multiple options

### 5. Ambiguity Handling
Multi-match scenario → deterministic disambiguation:

```python
result = resolver.resolve_terms("baseline vs actual")
# result.ambiguous == True
# result.ambiguity_question == "Did you mean 'baseline_cost' (Cost without...) 
#                               or 'actual_cost' (Cost after...)?"
```

## Glossary Format

Expected structure in `term_cards.json`:

```json
{
  "baseline_cost": {
    "term": "baseline_cost",
    "what": "Cost without compression — full uncompressed cost.",
    "who": "FinOps, budget owners",
    "where": ["finops dashboard", "cost cards"],
    "why": "Reference point for compression value",
    "how": "raw_input_tokens × input_rate + output_tokens × output_rate",
    "not_this": "Not actual spend — this is the counterfactual uncompressed cost",
    "aliases": ["baseline", "uncompressed cost", "full cost"],
    "tier": 0,
    "confidence": 1.0,
    "source_refs": ["finops_partial.html"]
  }
}
```

**Used by resolver:**
- `term`: Canonical term ID (key)
- `what`: Card meaning (injected)
- `aliases`: Lookup variants (indexed for matching)
- `tier`: Priority for ordering (higher = first)
- `confidence`: Quality score for ordering
- Other fields: available for future extensions

## Usage Examples

### Basic Resolution

```python
from tokenpak.agent.semantic import resolve_terms

result = resolve_terms("What is compression ratio?")
print(result.canonical_ids)      # ["compression_ratio"]
print(result.injection_text)     # Ready for prompt
print(result.tokens_estimate)    # 45 tokens
```

### Custom Configuration

```python
from tokenpak.agent.semantic import TermResolver, TermResolverConfig

config = TermResolverConfig(top_k=5, max_bytes_per_card=300)
resolver = TermResolver(config=config)
result = resolver.resolve_terms("baseline and actual costs")
```

### Proxy Integration (Safe Rollout)

```bash
# Stage 1: Deploy with flag disabled (zero overhead)
TOKENPAK_TERM_RESOLVER_ENABLED=0

# Stage 2: Enable for subset of traffic
TOKENPAK_TERM_RESOLVER_ENABLED=1

# Stage 3: Verify cache hits unchanged, no regression
# Monitor: /health endpoint, glossary tokens in logs
```

## Testing

### Unit Tests (19 tests)
- Basic term extraction (canonical + aliases)
- Deterministic resolution (repeated queries)
- Hard cap enforcement (top-K, bytes per card)
- Ambiguity detection
- No crash on missing cards
- Feature flag behavior

### Integration Tests (13 tests)
- Proxy initialization
- Health endpoint reporting
- Glossary + vault combination
- Cache stability (byte-identical runs)
- Zero overhead when disabled
- Runtime policy enforcement

### Running Tests

```bash
cd ~/Projects/tokenpak
python3 -m pytest tokenpak/agent/semantic/ -v
# 32 tests in 0.56s
```

## Performance

- **Load time**: ~5ms (term_cards.json parse + index build)
- **Resolution time**: <1ms per query (regex matching + sort)
- **Memory overhead**: ~500KB (glossary index + aliases)
- **Injection overhead**: Negligible (token budget reallocation only)

## Debugging

### Enable Logging (optional extension)

```python
result = resolver.resolve_terms(query)
if result.canonical_ids:
    print(f"Matched: {result.canonical_ids}")
    print(f"Tokens: {result.tokens_estimate}")
    if result.ambiguous:
        print(f"Ambiguous: {result.ambiguity_question}")
```

### Verify Cache Stability

```python
from tokenpak.agent.semantic.term_resolver import measure_injection_consistency

consistency = measure_injection_consistency(
    lambda q: resolver.resolve_terms(q),
    query="baseline cost",
    runs=5,
)
print(f"Consistent: {consistency['consistent']}")  # True = cache stable
```

## Acceptance Criteria (Met)

- [x] Runtime path uses resolver only when relevant terms detected
- [x] No full glossary injection; top-K + hard caps enforced
- [x] Ambiguous term handling is deterministic and test-covered
- [x] Equivalent text variants resolve to same canonical targets
- [x] Tests prove no regression to baseline when disabled
- [x] All 32 tests pass (unit + integration)

## Future Extensions

1. **Per-domain glossaries** (finance, engineering, legal)
2. **Learned term weights** from usage patterns
3. **Spelling correction** for aliases
4. **Semantic similarity fallback** for partial matches
5. **Multi-language glossaries**

## References

- Task: `<internal-agent-queue>/p1-tokenpak-runtime-term-card-resolver-2026-03-10.md`
- Glossary: `tokenpak/term_cards.json`
- Integration: `tokenpak/proxy/server.py` (vault injection path)
