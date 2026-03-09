# Type Hints Implementation Checklist

## Status Overview

- **Total errors:** 1588 (started: 1596)
- **Files affected:** 188/282
- **Target:** 0 errors in public API (full codebase follow-up)

## Implementation Checklist

### Public API Layer (Priority 1)

- [x] `Budgeter` class has full type hints
  - [x] `__init__() -> None`
  - [x] `allocate(components: Dict[str, Any]) -> Dict[str, Any]`
  - [x] `budget_report(components: Dict[str, Any]) -> Dict[str, Any]`
  - [x] All helper methods typed

- [ ] `CompressionEngine` base class
  - [ ] `compress() -> str`
  - [ ] `decompress() -> str`
  - [ ] All abstract methods have return type hints

- [ ] `Block` and `BlockRegistry` classes
  - [ ] All public methods typed
  - [ ] Constructor parameters typed

- [ ] `TelemetryCollector` / `CostTracker`
  - [ ] All tracking methods typed
  - [ ] Return types for metrics collection

- [ ] Module-level public functions
  - [ ] `get_engine(name: str) -> CompressionEngine`
  - [ ] `pack_prompt(...) -> ContextPack`
  - [ ] `count_tokens(text: str) -> int`

### Generic Type Parameters (Priority 2)

- [x] Fixed `dict` → `Dict[str, Any]` in public APIs
- [ ] Fix remaining `dict` in internal code
- [ ] Fix `list` → `list[T]` generics
- [ ] Fix `set`, `tuple`, `deque` generics
- [ ] Fix `Optional[T]` defaults (no implicit Optional)

### Return Type Annotations (Priority 3)

- [x] Added to major public functions
- [ ] Fix remaining missing return types in:
  - [ ] `telemetry/operational/metrics.py` (~5 functions)
  - [ ] `engines/heuristic.py` (~2 functions)
  - [ ] `connectors/base.py` (~3 functions)

### Type Incompatibilities (Priority 4)

- [ ] Fix `Returning Any` issues in:
  - [ ] `budgeter.py:91` (yaml.safe_load)
  - [ ] `watchdog.py:72` (dict load)
  - [ ] `integrations/litellm/parser.py:76` (Any return)

- [ ] Fix type mismatches:
  - [ ] `github.py:45,51` (None assignments)
  - [ ] `complexity.py:220` (implicit Optional)
  - [ ] `query_builder.py:207,209` (int vs str)

### Testing (Priority 5)

- [ ] All public API modules pass mypy --strict
- [ ] Type hint tests pass (if test_type_hints.py exists)
- [ ] IDE autocomplete verified in VSCode/PyCharm
- [ ] Type stubs (.pyi) created for hard-to-type modules

### Documentation (Priority 6)

- [x] Created `docs/type-hints.md`
- [x] Added README section on type hints
- [ ] Updated `CONTRIBUTING.md` with type hint guidelines
- [ ] Added examples to API documentation

## Per-Module Breakdown

### Core (Should be 100% typed)

| Module | Errors | Status | Action |
|--------|--------|--------|--------|
| budgeter.py | 6 | ⚠️ Partial | Fix `yaml.safe_load` Any returns |
| registry.py | ~8 | ⚠️ Partial | Type all Block methods |
| core.py | ~12 | ⚠️ Partial | Add return types |
| wire.py | 1 | ✅ Done | |
| report.py | 2 | ⚠️ Minimal | Add dict type params |

### Engines (Should be 100% typed)

| Module | Errors | Status | Action |
|--------|--------|--------|--------|
| engines/base.py | ~5 | ⚠️ Partial | Type abstract methods |
| engines/heuristic.py | 2 | ⚠️ Missing | Add return types |
| engines/llmlingua.py | 2 | ❌ External | Create .pyi stub |

### Telemetry (Should be 50%+ typed)

| Module | Errors | Status | Action |
|--------|--------|--------|--------|
| telemetry/cost_tracker.py | ~10 | ⚠️ Partial | Type methods |
| telemetry/query_models.py | 2 | ⚠️ Minimal | Add dict type params |
| telemetry/operational/metrics.py | 3 | ❌ Missing | Add return types |

### Connectors (Can be 25%+ typed)

| Module | Errors | Status | Action |
|--------|--------|--------|--------|
| connectors/base.py | 3 | ❌ Minimal | Add param/return types |
| connectors/github.py | 2 | ❌ Minimal | Fix None assignments |

### CLI (Can be 0% — auto-generated)

| Module | Errors | Status | Action |
|--------|--------|--------|--------|
| agent/cli/main.py | ~20 | ❌ Skip | Use Click decorators; auto-typing |

## Success Metrics

- ✅ All public API functions have full type hints
- ✅ All parameters have type annotations
- ✅ All functions have return type annotations
- ✅ Public API modules pass `mypy --strict`
- ✅ `docs/type-hints.md` comprehensive and up-to-date
- ✅ Type-checking examples in README

## Commit Strategy

Each phase should be a single commit:

```bash
# Phase 1 (public APIs)
git add -A
git commit -m "type-hints: public API layer complete — budgeter, registry, core"

# Phase 2 (generics)
git commit -m "type-hints: generic type parameters — dict, list, set, tuple"

# Phase 3 (return types)
git commit -m "type-hints: return type annotations across telemetry/engines"

# Phase 4 (fixes)
git commit -m "type-hints: fix type incompatibilities — yaml, None, Any"

# Phase 5 (testing)
git commit -m "type-hints: tests & mypy verification — 50%+ public API complete"
```

## Notes

- **Large codebase:** 282 files, 1588 errors. Focus on public API first.
- **External deps:** `llmlingua`, `yaml`, `tiktoken` — use type stubs for hard cases.
- **CLI:** Click decorators + auto-typing reduces manual work.
- **Progressive:** Target 50% in this sprint, 100% in follow-up.

---

*Last updated: 2026-03-09 (Task in-progress)*
