---
title: "PROXY-IMPORT-AUDIT.md — TokenPak proxy.py Import Tier Mapping"
status: complete
created: "2026-03-27"
author: Cali
traces_to:
  task: "p2-tokenpak-oss-pro-proxy-import-audit-2026-03-27"
  proposal: "02_COMMAND_CENTER/proposals/2026-03-27-tokenpak-oss-pro-structure-realignment.md"
  boundary: "PROXY-IMPORT-AUDIT.md"
---

# PROXY-IMPORT-AUDIT.md — Proxy.py Import Tier Mapping

**Date:** 2026-03-27  
**Author:** Cali  
**Status:** Complete (analysis only, no code changes)  
**Feeds Into:** Phase 2 (Trix feature gating work)

---

## Summary

**Total imports in proxy.py:** 2 unique `from tokenpak.*` imports  
**OSS-tier imports:** 2  
**Pro-tier imports:** 0  
**Enterprise-tier imports:** 0  
**Unclear imports:** 0

**Pro feature-gating backlog:** 0 imports in live proxy.py (surprisingly minimal!)

---

## Complete Import Audit

| Import | What | Tier | Justification |
|--------|------|------|---------------|
| `tokenpak.proxy.adapters.base` | `FormatAdapter` | OSS | Core format translation — OSS foundation |
| `tokenpak.proxy.adapters` | `build_default_registry` | OSS | Adapter registry builder — OSS foundation |

---

## Analysis

### OSS-Tier Modules (Live proxy.py uses)

```python
from tokenpak.proxy.adapters.base import FormatAdapter
from tokenpak.proxy.adapters import build_default_registry
```

Both imports are **OSS core**:

- **`tokenpak.proxy.adapters.base.FormatAdapter`** — Base class for format adapters (Anthropic, OpenAI, Google). Core abstraction layer. **Tier: OSS**
- **`tokenpak.proxy.adapters.build_default_registry()`** — Factory function to build the standard adapter registry. **Tier: OSS**

### What This Means

The **current live proxy.py** (`~/tokenpak/proxy.py`) has **zero Pro-tier imports**. All direct imports are OSS core.

This suggests:
1. **The running proxy is already OSS-compatible** (surprising, but good news)
2. Pro features may be invoked indirectly (through registries, plugin paths) rather than via direct imports
3. **Feature gating in Phase 2 may be lighter than expected** — we may not need to gate imports in proxy.py itself, but rather in the called functions

---

## Unclear Imports

**None.** Both imports map cleanly to OSS tier.

---

## Pro Feature Gating Backlog (Phase 2)

**Direct imports that need feature gating in proxy.py:** 0

**However, the OSS boundary analysis reveals these Pro modules are likely called indirectly:**

- `tokenpak.agent.compression.pipeline` (through adapter pipelines)
- `tokenpak.agent.compression.fidelity_tiers` (through compression recipe selection)
- `tokenpak.agent.agentic.*` (through workflow invocation)
- `tokenpak.intelligence.ab_router` (through routing rules evaluation)

**Phase 2 task for Trix:** Trace execution paths from `proxy.py` → Pro modules and add `feature_gates.py` checks at call sites (not import sites).

---

## Git Commit Hash

```bash
cd ~/vault
git add docs/PROXY-IMPORT-AUDIT.md
git commit -m "cali: proxy import audit — tier mapping for OSS/PRO realignment (2 imports, 100% OSS)" 
git push origin HEAD:main
```

**Expected hash:** (on next commit)

---

## Notes

- This is a **research task only** — no code changes made
- All proxy.py imports are currently OSS-tier
- Phase 2 will need to trace indirect calls and add runtime feature gating
- This is good news for OSS distribution — proxy core is clean
