# TokenPak v0.1 → v1.0 Migration Guide

**TL;DR:** v1.0 is fully backward compatible. No code changes required. Optional: use the new clean public API.

---

## What's New in v1.0

### Public API Surface
TokenPak now has a clean, documented public API. Import everything from the top-level package:

```python
from tokenpak import (
 TelemetryCollector,
 CompletionTracker,
 CacheManager,
 CompressionEngine,
 HeuristicEngine,
 Block,
 BlockRegistry,
 Budgeter,
 BudgetBlock,
 get_engine,
)
```

### New Features
- **Prompt caching** — Automatic Anthropic cache_control markers
- **Tool schema freezing** — 6-7K tokens saved per request on tool schemas
- **CANON deduplication** — Cross-turn content block deduplication
- **Cost/budget CLI** — `tokenpak cost`, `tokenpak budget` commands
- **Doctor command** — `tokenpak doctor` diagnoses issues
- **Replay system** — `tokenpak replay` to diff past requests

### Performance Improvements
- 27% average token reduction (production measured)
- 71.8% cache hit rate
- 25x tokenization speedup (LRU cache)

---

## Breaking Changes

**None!** v1.0 is fully backward compatible with v0.1.

All existing code will continue to work without modification.

---

## How to Upgrade

### Step 1: Uninstall v0.1
```bash
pip uninstall tokenpak
```

### Step 2: Install v1.0
```bash
pip install tokenpak==1.0.0
```

Or from source:
```bash
cd ~/Projects/tokenpak
pip install -e .
```

### Step 3: Verify
```bash
tokenpak doctor
```

Expected output:
```
TokenPak Doctor
===============
✅ Python version: 3.12.x
✅ tokenpak version: 1.0.0
✅ Required packages installed
✅ Configuration valid
```

---

## Import Changes (Optional)

### v0.1 Style (Still Works)
```python
# Deep imports — still supported
from tokenpak.telemetry.collector import TelemetryCollector
from tokenpak.engines.heuristic import HeuristicEngine
from tokenpak.registry import Block, BlockRegistry
```

### v1.0 Style (Recommended)
```python
# Clean top-level imports
from tokenpak import TelemetryCollector, HeuristicEngine, Block, BlockRegistry
```

Both styles work in v1.0. The top-level imports are preferred for:
- Cleaner code
- Better IDE autocomplete
- Forward compatibility

---

## Code Examples

### Telemetry Collection

**v0.1:**
```python
from tokenpak.telemetry.collector import TelemetryCollector

tc = TelemetryCollector()
tc.log_request(model="claude-3", tokens=1500, cost=0.05)
```

**v1.0 (same code works, or use new import):**
```python
from tokenpak import TelemetryCollector

tc = TelemetryCollector()
tc.log_request(model="claude-3", tokens=1500, cost=0.05)
```

### Compression Engine

**v0.1:**
```python
from tokenpak.engines.heuristic import HeuristicEngine

engine = HeuristicEngine(mode="hybrid")
result = engine.compress(text)
```

**v1.0:**
```python
from tokenpak import HeuristicEngine, get_engine

# Option 1: Direct instantiation
engine = HeuristicEngine(mode="hybrid")

# Option 2: Factory function (new)
engine = get_engine("heuristic", mode="hybrid")

result = engine.compress(text)
```

### Block Registry

**v0.1:**
```python
from tokenpak.registry import Block, BlockRegistry

registry = BlockRegistry(path="~/.tokenpak/blocks")
block = Block(id="abc", content="...", hash="...")
registry.add(block)
```

**v1.0 (identical):**
```python
from tokenpak import Block, BlockRegistry

registry = BlockRegistry(path="~/.tokenpak/blocks")
block = Block(id="abc", content="...", hash="...")
registry.add(block)
```

---

## New CLI Commands

v1.0 adds several CLI tools:

```bash
# View cost/usage reports
tokenpak cost
tokenpak cost --yesterday
tokenpak cost --week --by-model

# Set and monitor budgets
tokenpak budget set --daily 10 --monthly 200
tokenpak budget status
tokenpak budget alert --at 80

# Diagnose issues
tokenpak doctor
tokenpak doctor --fix

# Replay past requests
tokenpak replay list
tokenpak replay show <id>
tokenpak replay run <id> --diff
```

---

## Configuration Changes

### v0.1 Config (Still Works)
```json
{
 "mode": "hybrid",
 "vault_path": "~/.tokenpak/blocks"
}
```

### v1.0 Config (New Options)
```json
{
 "mode": "hybrid",
 "vault_path": "~/.tokenpak/blocks",
 "cache_control": true,
 "tool_schema_freeze": true,
 "canon_enabled": true,
 "budget": {
 "daily_limit": 10.0,
 "monthly_limit": 200.0,
 "alert_threshold": 0.8
 }
}
```

New options are optional — v0.1 configs work without modification.

---

## FAQ

### Q: Will my code break?
**A:** No. v1.0 is 100% backward compatible with v0.1.

### Q: Do I need to change my imports?
**A:** No, but you can. The new top-level imports are cleaner and recommended for new code.

### Q: What if I'm using internal APIs?
**A:** Internal APIs (anything not in `__all__`) may change in future versions. Consider migrating to the public API.

### Q: Where do I report issues?
**A:** GitHub Issues: https://github.com/tokenpak/tokenpak/issues

### Q: Where are the full docs?
**A:** See `/docs/` in the repo or https://tokenpak.dev (coming soon)

---

## Checklist

Before deploying v1.0:

- [ ] Uninstall v0.1: `pip uninstall tokenpak`
- [ ] Install v1.0: `pip install tokenpak==1.0.0`
- [ ] Run `tokenpak doctor` — all checks pass
- [ ] Run your tests — everything passes
- [ ] (Optional) Update imports to use top-level API
- [ ] (Optional) Enable new features (cache_control, canon, budgets)

---

## Need Help?

- **Docs:** `/docs/` directory
- **Issues:** https://github.com/tokenpak/tokenpak/issues
- **Changelog:** `/CHANGELOG.md`

Welcome to TokenPak v1.0! 🎉
