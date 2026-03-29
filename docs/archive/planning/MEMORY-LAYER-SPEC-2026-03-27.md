---
title: "Memory Layer Implementation Spec — Feasibility, Phases, and Task Breakdown"
date: 2026-03-27
author: Cali
project: tokenpak
tags: [memory, retrieval, architecture, phases]
---

# Memory Layer Implementation Spec

**Status:** Ready for implementation  
**Priority:** P2 (post-launch enhancement)  
**Proposal Basis:** 
- `proposal-tokenpak-memory-layer-2026-03-26.md` (Memory-first retrieval, decision memory, layered assembly)
- `proposal-tokenpak-memory-addressing-2026-03-26.md` (Case memory generalization, promotion triggering, temporal index)

---

## Executive Summary

TokenPak has four well-designed memory modules (`failure_memory.py`, `learning.py`, `memory_promoter.py`, `session_capsules.py`) that **exist but are never queried during request processing**. This spec breaks the proposal into three implementation phases:

1. **Phase 1 (Quick Wins — 4–6h):** Wire memory_promoter into background cron jobs; enable basic memory query in proxy
2. **Phase 2 (Core Value — 2–3 days):** Memory-first retrieval pipeline, decision memory store, promotion automation
3. **Phase 3 (Extensions — 3–5 days):** Case memory generalization, temporal indexing, learning loop integration

**Key Principle:** Memories should short-circuit vault search where possible, not add to it. Budget is zero-sum: memory tokens reduce vault budget.

---

## Feasibility Assessment

### Module Status

| Module | Exists? | Condition | Feasibility | Effort | Risk |
|--------|---------|-----------|-------------|--------|------|
| **failure_memory.py** | ✅ Yes | Well-built, 150 lines | High | —— | Low |
| **learning.py** | ✅ Yes | Well-built, bridges to memory_promoter | High | —— | Low |
| **memory_promoter.py** | ✅ Yes | Exists, BUT evaluate_pending() never called | Medium | +4h wiring | Medium |
| **session_capsules.py** | ✅ Yes | Exists, summarizes sessions | High | —— | Low |
| **case_memory.py** | ✅ Yes | Already generalized (not error-only) | High | —— | Low |
| **decision_memory.py** | ❌ No | Proposed new module | Medium | +6h new | Medium |

### Blocking Dependencies

- ✅ No infrastructure blockers
- ✅ All data structures defined
- ✅ Module imports available
- ✅ Storage backends ready (JSON, SQLite)
- ⚠️ Memory promoter cron job needs to be created (not exists)

### Estimated Effort Breakdown

| Phase | Component | Estimate | Difficulty |
|-------|-----------|----------|------------|
| **Phase 1** | Wire memory_promoter to cron job | 2h | Easy |
| | Create decision_memory.py skeleton | 2h | Easy |
| | Add feature flag for memory retrieval | 1h | Trivial |
| | **Phase 1 Total** | **5h** | **Easy** |
| **Phase 2** | Implement retrieve_memories() function | 4h | Medium |
| | Integrate into proxy.py _proxy_to() | 3h | Medium |
| | Build decision memory learning loop | 3h | Medium |
| | Write unit tests for retrieval | 2h | Easy |
| | **Phase 2 Total** | **12h** | **Medium** |
| **Phase 3** | Case memory generalization (if not done) | 3h | Medium |
| | Temporal event index | 4h | Medium |
| | Learning integration (confidence scoring) | 3h | Medium |
| | **Phase 3 Total** | **10h** | **Medium** |

**Total estimate:** 27 hours of development, 60% in Phase 2 (core value).

---

## Phase 1: Quick Wins (4–6 hours)

**Goal:** Enable memory_promoter and create scaffolding for memory-first retrieval.

### Phase 1.1: Memory Promoter Cron Job (2h)

**What:** Create `~/vault/06_RUNTIME/cron/memory-promoter-eval.sh` to periodically call `memory_promoter.evaluate_pending()`.

**Why:** `memory_promoter.py` has proper tier gates (2+ occurrences, 70% success rate, 7-day no-contradiction, 15% savings threshold) but nothing triggers evaluation. Memories stay in Tier 1 forever.

**Implementation:**

```bash
# ~/vault/06_RUNTIME/cron/memory-promoter-eval.sh
#!/bin/bash
set -eu

# Run memory promoter evaluation every 6 hours
cd ~/vault/01_PROJECTS/tokenpak
source venv/bin/activate

python3 << 'EOF'
from tokenpak.agent.agentic.memory_promoter import MemoryPromoter
from pathlib import Path

promoter = MemoryPromoter(storage_dir=Path.home() / ".tokenpak")
promoted_count = promoter.evaluate_pending()
print(f"✨ Memory promoter: {promoted_count} memories promoted")
EOF
```

**Added to crontab:** `0 */6 * * * bash ~/vault/06_RUNTIME/cron/memory-promoter-eval.sh >> ~/logs/memory-promoter.log 2>&1`

**Testing:** Run once manually, verify `.tokenpak/memory_promoter.json` shows tier changes.

---

### Phase 1.2: Decision Memory Module (2h)

**What:** Create `/tokenpak/packages/core/tokenpak/agent/agentic/decision_memory.py` (if not already complete).

**Why:** Decisions need a dedicated store separate from failure_memory (errors) and learning.py (metrics).

**Schema:**

```python
@dataclass
class DecisionRecord:
    decision_id: str           # "dec-bm25-vault-20260326"
    decision: str              # "Use BM25 over embeddings for vault search"
    rationale: str             # "3 agents, 4GB RAM, <10K blocks, no GPU"
    made_at: datetime          # When decision was made
    confidence: float          # 0.0-1.0 (updated by learning loop)
    superseded_by: Optional[str]  # If later decision overrides this
    sources: List[str]         # Vault block IDs that informed this
    impacts: List[str]         # ["vault_search", "compression_strategy"]

class DecisionMemory:
    def record(self, decision: str, rationale: str, sources: List[str]) -> DecisionRecord:
        """Record a new decision."""
    
    def search(self, query: str, top_k: int = 3) -> List[DecisionRecord]:
        """Find decisions relevant to a query."""
    
    def get_active(self, topic: str) -> Optional[DecisionRecord]:
        """Get current active decision for a topic."""
```

**Storage:** `~/.tokenpak/decision_memory.json` (append-only log of decisions).

**Testing:** `test_decision_memory.py` with 3 test cases: record, search, supersede.

---

### Phase 1.3: Feature Flag (1h)

**What:** Add to `openclaw.json` or environment:

```json
{
  "tokenpak": {
    "memory_retrieval_enabled": false,
    "memory_budget": 400
  }
}
```

**Or environment variable:** `TOKENPAK_MEMORY_RETRIEVAL=0` (default off, feature-flagged).

**Why:** Allows safe rollout without affecting performance until Phase 2 is complete and tested.

---

### Phase 1 Deliverables

- [ ] `memory-promoter-eval.sh` script created
- [ ] Cron job added and tested
- [ ] `decision_memory.py` module complete with schema + 3 core methods
- [ ] Feature flag in config with `memory_retrieval_enabled=false`
- [ ] Unit tests pass: `pytest test_decision_memory.py`
- [ ] Commit: "feat: Phase 1 memory scaffolding — promoter cron + decision memory"

---

## Phase 2: Core Value (10–14 hours)

**Goal:** Implement memory-first retrieval pipeline and integrate into proxy.

### Phase 2.1: Memory Retrieval Function (4h)

**What:** Implement `retrieve_memories(query: str, budget: int) -> Tuple[str, int, List[str]]` in `proxy.py`.

**Pseudocode:**

```python
def retrieve_memories(query: str, budget: int = 400) -> Tuple[str, int, List[str]]:
    """Query memory stores in priority order; return injection text + tokens used + source IDs."""
    results = []
    tokens_used = 0
    
    # 1. Decision memory (highest priority)
    try:
        decisions = DECISION_MEMORY.search(query, top_k=3)
        for d in decisions:
            if d.superseded_by:
                continue  # Skip superseded decisions
            text = f"[DECISION] {d.decision}\nRationale: {d.rationale}"
            t = token_count(text)
            if tokens_used + t <= budget:
                results.append(text)
                tokens_used += t
    except Exception as e:
        log(f"Decision memory search failed: {e}")
    
    # 2. Failure memory (if error-related)
    if _looks_like_error(query):
        try:
            recipes = FAILURE_MEMORY.match(query)
            if recipes and recipes[0].confidence > 0.6:
                text = f"[REPAIR] {recipes[0].error_class}: {', '.join(recipes[0].repair_recipe)}"
                t = token_count(text)
                if tokens_used + t <= budget:
                    results.append(text)
                    tokens_used += t
        except Exception as e:
            log(f"Failure memory search failed: {e}")
    
    # 3. Promoted memories (Tier 3+)
    try:
        promoted = MEMORY_PROMOTER.recall(query, min_tier=3, top_k=2)
        for m in promoted:
            text = f"[MEMORY] {m.summary}"
            t = token_count(text)
            if tokens_used + t <= budget:
                results.append(text)
                tokens_used += t
    except Exception as e:
        log(f"Memory promoter recall failed: {e}")
    
    injection = "\n".join(results)
    source_ids = [r.get("id") for r in results]
    return injection, tokens_used, source_ids
```

**Key points:**
- Graceful error handling (memory store failure ≠ request failure)
- Token budget is strict (no overage)
- Skip superseded decisions
- Order by value density (decisions first, then repairs, then promotions)

---

### Phase 2.2: Proxy Integration (3h)

**What:** Wire `retrieve_memories()` into `_proxy_to()` function as Phase 0.7 (between capsule builder and vault injection).

**Location in code:** `proxy.py`, line ~3650 (before `inject_vault_context()` call).

**Pseudocode integration:**

```python
# In _proxy_to(), after phase 0.5 (capsule builder):

# Phase 0.7: Memory retrieval (feature-flagged)
memory_injection = ""
memory_tokens = 0
memory_refs = []

if MEMORY_RETRIEVAL_ENABLED and token_budget > 100:
    memory_injection, memory_tokens, memory_refs = retrieve_memories(
        query_signal, budget=min(MEMORY_BUDGET, token_budget - 100)
    )
    if memory_tokens > 0:
        print(f"  🧠 Memory: {memory_tokens} tokens from {len(memory_refs)} sources")
        token_budget -= memory_tokens

# Phase 1: Vault injection (with reduced budget)
if VAULT_INDEX.available:
    vault_injection, vault_tokens, vault_refs = inject_vault_context(
        body, adapter=active_adapter, budget=token_budget
    )
    body = vault_injection
    token_budget -= vault_tokens
else:
    vault_tokens = 0
    vault_refs = []

# Combine injections
final_body = memory_injection + "\n" + body if memory_injection else body

# Log memory + vault breakdown
trace["memory"] = {"tokens": memory_tokens, "sources": memory_refs}
trace["vault"] = {"tokens": vault_tokens, "sources": vault_refs}
```

**Testing:**
- Memory on + vault on → should reduce vault tokens used
- Memory off → should be no change to vault behavior
- Memory budget > remaining budget → should use only what's left

---

### Phase 2.3: Decision Memory Learning Loop (3h)

**What:** Wire learning feedback back into decision memory to update confidence scores.

**How:** When a request with memory injection succeeds, increment decision confidence; if it fails, decrement.

**Integration point:** `post_run.py` or `telemetry/collector.py`.

**Pseudocode:**

```python
def learn_from_decision(decision_id: str, success: bool, outcome_quality: float = 1.0):
    """Update decision confidence based on outcome."""
    record = DECISION_MEMORY.get(decision_id)
    if not record:
        return
    
    # Exponential moving average: new_conf = 0.8 * old + 0.2 * outcome
    old_conf = record.confidence
    new_conf = 0.8 * old_conf + 0.2 * (1.0 if success else 0.0) * outcome_quality
    
    DECISION_MEMORY.update(decision_id, confidence=new_conf)
    
    # If confidence drops below 0.5, suggest supersession
    if new_conf < 0.5:
        log(f"⚠️ Decision {decision_id} confidence dropped to {new_conf:.2f}")
```

**Testing:** Simulate 10 successes → confidence rises to ~0.97; 5 failures → drops.

---

### Phase 2.4: Unit Tests (2h)

**What:** Write `test_memory_retrieval.py` covering:

- [ ] `retrieve_memories()` with mixed stores active
- [ ] Token budget enforcement (no overage)
- [ ] Error handling (memory store down ≠ request fails)
- [ ] Superseded decision skipping
- [ ] Feature flag toggle (on/off)
- [ ] Vault budget reduction when memory used

**Target:** 10–15 test cases, >90% code coverage on retrieval function.

---

### Phase 2 Deliverables

- [ ] `retrieve_memories()` function complete, tested, graceful error handling
- [ ] Integrated into `_proxy_to()` as Phase 0.7 (feature-flagged)
- [ ] Decision memory learning loop in `post_run.py`
- [ ] Unit tests pass: `pytest test_memory_retrieval.py -v`
- [ ] Feature flag toggle tested: both on and off
- [ ] Trace logging shows memory + vault breakdown
- [ ] Commit: "feat: Phase 2 memory-first retrieval pipeline"

---

## Phase 3: Extensions (8–12 hours)

### Phase 3.1: Case Memory Generalization (3h)

**Current state:** `case_memory.py` exists and supports multiple case types ("error", "decision", "workflow", "lesson", "anti-pattern").

**What:** Wire case memory into `retrieve_memories()` so non-error cases are also surfaced.

**Implementation:** Add case memory search between decision memory and failure memory in retrieval priority.

**Testing:** Query "what's the pattern for handling async migration?" → should surface a "lesson" type case record.

---

### Phase 3.2: Temporal Event Index (4h)

**What:** Create `temporal_index.py` — lightweight event log keyed by date + tags.

**Schema:**

```python
@dataclass
class TemporalEvent:
    event_id: str
    timestamp: datetime
    title: str
    body: str
    tags: List[str]         # ["proxy", "fix", "regression", ...]
    references: List[str]   # Block IDs, decision IDs, case IDs

class TemporalIndex:
    def record(self, title: str, body: str, tags: List[str], refs: List[str]):
        """Record an event."""
    
    def events_since(self, days_ago: int) -> List[TemporalEvent]:
        """Get events from past N days."""
    
    def search_by_tag(self, tag: str, limit: int = 10) -> List[TemporalEvent]:
        """Find events by tag."""
```

**Use case:** "What changed after the proxy fix on 2026-03-26?" → query temporal index by tag "proxy-fix".

**Storage:** `~/.tokenpak/temporal_index.jsonl` (append-only log).

---

### Phase 3.3: Learning Integration (3h)

**What:** Wire learning.py metrics into memory promotion scoring.

**How:** When a memory is promoted, include metrics snapshot (compression ratio, success rate, token savings).

**Testing:** Promote a memory → verify metrics are attached; later queries should see the improvement rationale.

---

### Phase 3 Deliverables

- [ ] Case memory integrated into memory retrieval
- [ ] Temporal index module complete + tested
- [ ] Learning metrics attached to promoted memories
- [ ] Unit tests: `pytest test_case_memory.py test_temporal_index.py -v`
- [ ] Commit: "feat: Phase 3 extensions — case memory, temporal index, learning metrics"

---

## Task Packets for Immediate Work

### Task 1: Memory Promoter Automation
- **Assignee:** Cali or Trix
- **Scope:** Create cron job + test manually
- **Effort:** 2h
- **Acceptance:** `memory_promoter.json` shows tier changes after running script

### Task 2: Decision Memory Module
- **Assignee:** Cali
- **Scope:** Create module, schema, 3 core methods, unit tests
- **Effort:** 2–3h
- **Acceptance:** `pytest test_decision_memory.py` passes; schema matches proposal

### Task 3: Memory Retrieval Integration
- **Assignee:** Cali
- **Scope:** Implement `retrieve_memories()`, wire into proxy Phase 0.7, test with feature flag
- **Effort:** 4–6h
- **Acceptance:** Proxy logs show `🧠 Memory: X tokens` when memory found; vault budget reduced accordingly

### Task 4: Learning Loop
- **Assignee:** Trix (post-Phase 2)
- **Scope:** Wire decision confidence updates into `post_run.py`, test with simulated outcomes
- **Effort:** 2–3h
- **Acceptance:** Confidence scores visible in decision memory; rise on success, fall on failure

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Memory store corruption | Low | Medium | Graceful fallback; requests work without memory |
| Token budget exhaustion | Low | Low | Strict budget enforcement; max memory_budget = 400 |
| Slow memory lookup (O(n)) | Medium | Low | Limit memory store size; implement indexing in Phase 3 |
| Decision memory poisoning | Low | Low | Confidence scoring + manual review before high-confidence decisions |
| Feature flag not toggled off | Low | Low | Default off; manual testing required before enabling |

---

## Success Criteria

**Phase 1 (Quick Wins):**
- [ ] Cron job runs without errors
- [ ] Decision memory module complete + tested
- [ ] Feature flag default is `false`

**Phase 2 (Core Value):**
- [ ] Requests with memory injection run <5% slower than without
- [ ] Memory retrieval returns relevant results (manual spot-check 5 queries)
- [ ] Vault tokens reduced by average 15–25% when memory active
- [ ] Zero requests fail due to memory layer outage

**Phase 3 (Extensions):**
- [ ] Case memory surfaces architectural/lesson decisions
- [ ] Temporal index answers "what changed?" queries
- [ ] Learning loop increases decision confidence over time (trending up on successful decisions)

---

## Implementation Order

1. **Week 1:** Phase 1 (cron + decision module + feature flag) — 5h
2. **Week 2:** Phase 2 (retrieval pipeline) — 10–14h
3. **Week 3+:** Phase 3 (extensions) — 8–12h

**Blocking note:** Phase 2 should not start until Phase 1 decision module is complete and tested.

---

## Next Steps

1. Create task packets (Task 1–4 above)
2. Cali: Claim Task 1 + 2 (Phase 1)
3. After Phase 1 approval: Cali claims Task 3 (retrieval integration)
4. Trix: Post-Phase 2, claims Task 4 (learning loop)
5. After Phase 2 approval: Phase 3 extensions can proceed in parallel

---

**Document version:** 1.0  
**Created:** 2026-03-27  
**Author:** Cali  
**Status:** Ready for implementation
