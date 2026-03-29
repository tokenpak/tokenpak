---
title: "TokenPak Test Coverage Analysis — 2026-03-27"
date: 2026-03-27
status: analysis
tags: [tokenpak, testing, quality, coverage]
---

# TokenPak Test Coverage Analysis

**Report Date:** 2026-03-27  
**Analysis Scope:** tokenpak package core modules  
**Coverage Tool:** pytest-cov  

## Executive Summary

This report identifies critical gaps in test coverage for the TokenPak package. The full test suite encounters import/compatibility issues (FastAPI `on_startup` deprecation), but isolated testing of new decision memory module shows strong coverage (97%). This analysis recommends targeted test additions for critical decision paths and modules.

## Test Infrastructure Status

### Current Issues
- FastAPI version incompatibility in `tokenpak.agent.ingest.api` (expects `on_startup` parameter)
  - Affects: `proxy/test_prompt_pack.py`, `proxy/test_token_count_cache.py`, `proxy/test_vault_search.py`, `test_models.py`
  - Blocker: Full coverage report generation
  - **Recommendation:** Update FastAPI router initialization to use `lifespan` parameter (FastAPI 0.93+)

### Decision Memory Module (Baseline)
```
Name                                       Stmts   Miss  Cover   Missing
tokenpak/agent/memory/decision_memory.py     113      3    97%   65, 168, 253
```

**Coverage: 97% (21/21 tests passing)**

Missing lines:
- Line 65: Error path in `record()` when query hashing fails (edge case)
- Line 168: Error path in `retrieve()` parameter validation
- Line 253: Edge case in `clear()` with locked database

## 5 Lowest-Coverage Modules (Identified)

Based on code inspection and known testing status:

| Module | Estimated Coverage | Files | Issue |
|--------|-------------------|-------|-------|
| `tokenpak.agent.ingest.api` | <20% | api.py | FastAPI incompatibility; no tests run |
| `tokenpak.agent.proxy.router` | <40% | router.py | Cost estimation, provider selection uncovered |
| `tokenpak.agent.vault.retrieval` | <50% | retrieval.py | Search logic, ranking uncovered |
| `tokenpak.agent.vault.indexer` | <60% | indexer.py | Index building, refresh logic |
| `tokenpak.models` | <70% | models.py | Edge cases in compression stats |

---

## Recommendations: 3 Targeted Test Additions

### 1. **Test: Error Handling in Decision Memory Hash Failures** (Priority: HIGH)

**Module:** `tokenpak.agent.memory.decision_memory`  
**Current Coverage Gap:** Lines 65, 168  
**Impact:** 2-3% coverage gain → 100%

**What to Test:**
- Mock `hashlib.sha256()` to raise `Exception` → verify `record()` behavior
- Mock database connection to fail → verify `retrieve()` gracefully handles missing results
- Verify confidence boundaries stay within [0.0, 1.0] when database corruption occurs

**Effort:** 1 hour | **Payoff:** Critical confidence scoring reliability

**Test Skeleton:**
```python
def test_record_hash_failure(temp_db, monkeypatch):
    """Test record() when hashlib fails."""
    monkeypatch.setattr("hashlib.sha256", lambda x: (_ for _ in ()).throw(Exception("hash error")))
    # Should log error and return None or raise
    
def test_retrieve_db_corruption(temp_db, monkeypatch):
    """Test retrieve() with corrupted database."""
    # Delete index, verify graceful fallback
```

---

### 2. **Test: Cost Estimation Provider Routes** (Priority: HIGH)

**Module:** `tokenpak.agent.proxy.router`  
**Current Coverage Gap:** ~60% of provider routing logic  
**Impact:** 15-20% coverage gain

**What to Test:**
- Routing decisions for different provider types (Anthropic, OpenAI, Gemini)
- Cost estimation accuracy (tokens → dollars)
- Fallback behavior when primary provider unavailable
- Rate limit handling edge cases

**Effort:** 3-4 hours | **Payoff:** Prevents production cost estimation bugs; critical for proxy reliability

**Test Skeleton:**
```python
def test_router_provider_selection():
    """Test provider selection logic for different models."""
    router = ProviderRouter()
    
    # Test Anthropic model routing
    assert router.select("claude-opus-4")[0].name == "anthropic"
    
    # Test GPT routing
    assert router.select("gpt-4")[0].name == "openai"
    
def test_cost_estimation_accuracy():
    """Test cost calculation accuracy."""
    cost = estimate_cost("gpt-4", tokens=1000)
    assert cost > 0
    assert cost < 1.0  # sanity bound
```

---

### 3. **Test: Vault Index Refresh and Search Ranking** (Priority: MEDIUM)

**Module:** `tokenpak.agent.vault.indexer` + `retrieval`  
**Current Coverage Gap:** ~40% of ranking/refresh logic  
**Impact:** 10-15% coverage gain

**What to Test:**
- Index building from scratch (new vault blocks)
- Incremental index refresh (block updates)
- Search ranking relevance (top-k ordering)
- Index corruption recovery

**Effort:** 2-3 hours | **Payoff:** Ensures vault search quality; prevents stale data retrieval

**Test Skeleton:**
```python
def test_index_build_from_empty():
    """Test building index from zero state."""
    indexer = VaultIndexer()
    blocks = [{"id": "b1", "content": "test"}]
    
    index = indexer.build(blocks)
    assert len(index) == 1
    
def test_search_ranking_order():
    """Test that search returns results in relevance order."""
    # Index multiple blocks with varying relevance
    results = indexer.search("test query", top_k=5)
    assert results[0]["score"] >= results[-1]["score"]
```

---

## FastAPI Compatibility Fix (URGENT)

### Root Cause
FastAPI 0.93+ removed `on_startup` and `on_shutdown` parameters. Code expects these for router initialization.

**File:** `tokenpak/agent/ingest/api.py:115`

**Current Code:**
```python
router = APIRouter(
    tags=["ingest"],
    on_startup=...,  # ❌ Deprecated
    on_shutdown=...  # ❌ Deprecated
)
```

**Fix:**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    # startup
    yield
    # shutdown

app = FastAPI(lifespan=lifespan)
```

**Estimated Effort:** 1-2 hours | **Blocks:** Full test suite

---

## Action Items

| Item | Owner | Due | Status |
|------|-------|-----|--------|
| Fix FastAPI `lifespan` compatibility | Engineering | Sprint end | 🔴 BLOCKED |
| Add decision memory error tests | Cali | This sprint | ✅ READY |
| Add provider router tests | Engineering | Next sprint | 📋 BACKLOG |
| Add vault indexer tests | Engineering | Next sprint | 📋 BACKLOG |

---

## Coverage Metrics Summary

| Module | Coverage | Status |
|--------|----------|--------|
| decision_memory | **97%** | ✅ Strong |
| Case Memory | Unknown | 🔴 Test import blocked |
| Router | <40% | ❌ Weak |
| Indexer | <60% | ❌ Weak |
| Vault Retrieval | <50% | ❌ Weak |

**Overall:** Estimated 45-55% across core modules (limited by FastAPI blocker).

---

*Generated: 2026-03-27 by Cali | Next review: post-FastAPI fix*
