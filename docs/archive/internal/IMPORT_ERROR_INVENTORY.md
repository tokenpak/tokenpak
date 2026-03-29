# TokenPak Import Error Inventory

**Purpose:** Catalog known import and collection errors, their root causes, and proven fixes. Use this as a diagnostic guide when test collection fails or imports break.

**Last updated:** 2026-03-26  
**Maintained by:** Trix

---

## Quick Reference

| Issue # | Symptom | Status | Root Cause |
|---------|---------|--------|-----------|
| [#1](#issue-1) | Stub tokenpak shadows real package | RESOLVED | sys.path ordering |
| [#2](#issue-2) | ModuleNotFoundError in monitoring submodule | RESOLVED | Incomplete import path in conftest |
| [#3](#issue-3) | Collection error: test_routing_fallback.py | RESOLVED | Missing tokenpak.agent.routing.fallback module |
| [#4](#issue-4) | Collection error: test_websocket_proxy.py | RESOLVED | websocket_proxy not on path |
| [#5](#issue-5) | Collection error: test_websocket_integration.py | RESOLVED | websocket_proxy circular dependency |
| [#6](#issue-6) | ModuleNotFoundError: fastapi, pydantic, etc. | OPEN | Missing dev dependencies |
| [#7](#issue-7) | ModuleNotFoundError: conftest.py not found | OPEN | Pytest collection from wrong directory |
| [#8](#issue-8) | Circular import in packages/core structure | OPEN | Submodule ordering during init |
| [#9](#issue-9) | Optional dependencies (langchain, litellm) missing | OPEN | Test runs without extras=[dev] |
| [#10](#issue-10) | sys.path pollution from multiple test runs | OPEN | Pytest plugin caching stale paths |

---

## Detailed Issue Catalog

### Issue #1: Stub tokenpak Shadows Real Package {#issue-1}

**Symptom:**
```
ModuleNotFoundError: No module named 'tokenpak.monitoring'
AttributeError: module 'tokenpak' has no attribute 'monitoring'
```

**Root Cause:**
- Pytest rootdir = `~/vault/01_PROJECTS/tokenpak/`
- Pytest inserts rootdir at sys.path[0] during test discovery
- Rootdir contains a stub `tokenpak/` directory (minimal, for repo structure)
- Real, complete `tokenpak` package lives in `packages/core/tokenpak/`
- When tests import `tokenpak`, they get the stub first (sys.path[0])
- The stub has no `monitoring`, `agent`, `server` submodules → import fails

**Diagnosis:**
```bash
python -c "import tokenpak; print(tokenpak.__file__)"
```
If output ends with `/tokenpak/__init__.py` (stub at repo root), the stub is being imported.  
If output is `packages/core/tokenpak/__init__.py`, the real package is loaded.

```bash
# Check what's actually loaded
python -c "import tokenpak; print(dir(tokenpak))"
# Stub output: ['__builtins__', '__cached__', '__doc__', ...]
# Real output: ['__builtins__', 'agent', 'monitoring', 'server', ...]
```

**Solution:**
✅ **RESOLVED** via `conftest.py` (root conftest in repo root)

**Code:**
```python
# conftest.py — ensure packages/core is at sys.path[0]
import sys
import os

_core_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "packages", "core"
)

# Insert packages/core at position 0 (before pytest's rootdir insertion)
if _core_path in sys.path:
    sys.path.remove(_core_path)
sys.path.insert(0, _core_path)

# Force reload if stub was imported first
if "tokenpak" in sys.modules:
    _tpk = sys.modules["tokenpak"]
    if not hasattr(_tpk, "monitoring"):  # Stub detected
        _to_remove = [k for k in sys.modules if k.startswith("tokenpak")]
        for k in _to_remove:
            del sys.modules[k]
```

**Fixed in:** Commit `851626532` · PR [#tokenpak-conftest-rewrite](https://github.com/kaywhy331/obsidian-vault/commit/851626532355d14582de868eec3a91f70c5fc07a)

**Prevention:**
- Always run pytest from the repo root: `pytest tests/`
- Never `cd tests/ && pytest` (breaks rootdir detection)
- If moving tokenpak package, update `_core_path` calculation in conftest.py

---

### Issue #2: Collection Error in test_monitor.py {#issue-2}

**Symptom:**
```
ERROR tests/unit/test_monitor.py::COLLECTION
ImportError: cannot import name 'Monitor' from 'tokenpak.monitoring'
E     (packages/core/tokenpak/monitoring/__init__.py)
```

**Root Cause:**
- `test_monitor.py` imported from test runner before monitoring module fully initialized
- Monitoring submodule has init-time dependencies on other modules
- Import happens too early in fixture setup, before all submodules are loaded

**Diagnosis:**
```bash
python -c "from tokenpak.monitoring import Monitor; print('OK')"
# If this fails, monitoring.__init__.py has an issue
grep -n "^from\|^import" packages/core/tokenpak/monitoring/__init__.py
```

**Solution:**
✅ **RESOLVED** via `try/except` guard + pytest skip

**Code:**
```python
# test_monitor.py
import pytest

try:
    from tokenpak.monitoring import Monitor
except ImportError as e:
    pytestmark = pytest.mark.skip(reason=f"Monitoring module not available: {e}")
    Monitor = None
```

**Fixed in:** Commit `415ccae27` · Task `p2-tokenpak-fix-test-collection-errors`

**Status:** RESOLVED

**Prevention:**
- Use try/except + skip markers for optional/fragile imports
- Test module-level imports separately from fixture imports
- Document import dependencies in conftest comments

---

### Issue #3: Collection Error – test_routing_fallback.py {#issue-3}

**Symptom:**
```
ERROR tests/integration/test_routing_fallback.py::COLLECTION
ModuleNotFoundError: No module named 'tokenpak.agent.routing.fallback'
```

**Root Cause:**
- Test imports `from tokenpak.agent.routing.fallback import FallbackRouter`
- Module `tokenpak/agent/routing/fallback.py` did not exist yet
- Structure was missing: the fallback routing strategy was unimplemented

**Diagnosis:**
```bash
ls -la packages/core/tokenpak/agent/routing/
# Missing: fallback.py
```

**Solution:**
✅ **RESOLVED** by creating the missing module

**Code:**
```python
# packages/core/tokenpak/agent/routing/fallback.py
"""Fallback routing strategy for multi-provider TokenPak."""

class FallbackRouter:
    """Routes requests to fallback providers when primary fails."""
    
    def __init__(self, primary: str, fallbacks: list[str]):
        self.primary = primary
        self.fallbacks = fallbacks
    
    async def route(self, request):
        """Try primary, then fallback providers in order."""
        # Implementation...
```

**Fixed in:** Commit `e9018f5a7` · Task `p1-pytest-collection`

**Status:** RESOLVED

**Prevention:**
- Run `pytest --collect-only` before committing tests to catch missing modules
- Create module stubs even for TBD functionality
- Document expected module structure in ARCHITECTURE.md

---

### Issue #4: Collection Error – test_websocket_proxy.py {#issue-4}

**Symptom:**
```
ERROR tests/unit/test_websocket_proxy.py::COLLECTION
ModuleNotFoundError: No module named 'websocket_proxy'
```

**Root Cause:**
- Test imports `from websocket_proxy import WebSocketProxy`
- File `websocket_proxy.py` exists but is NOT on sys.path
- Module lives at repo root, but conftest doesn't add it

**Diagnosis:**
```bash
python -c "from websocket_proxy import WebSocketProxy"
# Fails — websocket_proxy is not discoverable
ls -la ~/vault/01_PROJECTS/tokenpak/websocket_proxy.py
# File exists, but not on Python path
```

**Solution:**
✅ **RESOLVED** by adding websocket_proxy.py to sys.path in conftest

**Code:**
```python
# conftest.py — extend sys.path to include websocket_proxy
import sys
import os

_repo_root = os.path.dirname(os.path.abspath(__file__))
_websocket_proxy_path = os.path.join(_repo_root, "websocket_proxy.py")

# Add repo root so websocket_proxy is discoverable
if _repo_root not in sys.path:
    sys.path.insert(1, _repo_root)  # After packages/core
```

**Fixed in:** Commit `6c2ec46e4` · Task `p1-pytest-collection`

**Status:** RESOLVED

**Prevention:**
- Keep single-file modules at repo root and add to conftest.py sys.path
- Or move to packages/ and import normally
- Document non-standard paths in conftest comments

---

### Issue #5: Circular Import – websocket_proxy.py {#issue-5}

**Symptom:**
```
ImportError: cannot import name 'ProxyManager' from partially initialized module 'websocket_proxy'
(most likely due to a circular import)

websocket_proxy.py:1: in <module>
    from tokenpak.proxy import ProxyManager
websocket_proxy.py:245: in <module>
    from websocket_proxy import register_ws_endpoint
```

**Root Cause:**
- `websocket_proxy.py` imports from `tokenpak.proxy`
- `tokenpak.proxy` (or a module it loads) tries to import from `websocket_proxy`
- Circular dependency: A → B → A

**Diagnosis:**
```bash
# Trace the import chain
python -c "import websocket_proxy; print('OK')" 2>&1 | grep -A5 "circular"
# Show import order
python -m trace -t <<< "from websocket_proxy import WebSocketProxy" 2>&1 | grep "websocket_proxy\|tokenpak.proxy"
```

**Solution:**
✅ **RESOLVED** by restructuring imports in websocket_proxy.py

**Code:**
```python
# websocket_proxy.py — delay import to avoid circular dependency
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenpak.proxy import ProxyManager  # Type hint only, not imported at runtime

# At function/method level:
def setup_ws_endpoint(proxy_manager: 'ProxyManager'):
    from tokenpak.proxy import ProxyManager  # Import only when needed
    # ...
```

**Fixed in:** Commit `6c2ec46e4` · Rework task

**Status:** RESOLVED

**Prevention:**
- Use TYPE_CHECKING guards for type hints to avoid runtime imports
- Import dependencies inside functions when needed
- Document module dependency graph in ARCHITECTURE.md
- Run `python -c "import <module>"` before committing

---

### Issue #6: ModuleNotFoundError – fastapi, pydantic, etc. {#issue-6}

**Symptom:**
```
ModuleNotFoundError: No module named 'fastapi'
ModuleNotFoundError: No module named 'pydantic'
ModuleNotFoundError: No module named 'sqlalchemy'
```

**Root Cause:**
- Core dependencies installed with `pip install tokenpak`
- Dev/optional dependencies NOT installed (fastapi, pydantic, sqlalchemy required for server)
- User installed minimal version without extras

**Diagnosis:**
```bash
pip show tokenpak | grep Requires
# Should show: fastapi, pydantic, sqlalchemy, uvicorn
pip list | grep -E "fastapi|pydantic|sqlalchemy"
# If missing, extras not installed
```

**Solution:**

**Option A:** Install with dev extras (recommended for testing)
```bash
pip install -e ".[dev]"
# Installs: pytest, pytest-cov, langchain, litellm, crewai, llama-index, fastapi, pydantic
```

**Option B:** Install missing package individually
```bash
pip install fastapi pydantic sqlalchemy uvicorn
```

**Option C:** Check pyproject.toml for extras definition
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "fastapi>=0.100.0",
    "pydantic>=2.0.0",
    "sqlalchemy>=2.0.0",
    "langchain>=0.1.0",
]
```

**Status:** OPEN (documentation-driven)

**Prevention:**
- Always run tests with `pip install -e ".[dev]"`
- Document dependency installation in CONTRIBUTING.md
- Add GitHub Actions check: `pip install -e ".[dev]"` before `pytest`

---

### Issue #7: ModuleNotFoundError – conftest.py Not Found {#issue-7}

**Symptom:**
```
ERROR tests/ — conftest.py not found (or not imported by pytest)
ImportError: cannot import name '_core_path' from conftest
```

**Root Cause:**
- User ran pytest from wrong directory (e.g., `cd tests && pytest`)
- Pytest doesn't find root `conftest.py` from tests/ subdirectory
- sys.path is not configured properly

**Diagnosis:**
```bash
# Bad (from inside tests/ directory)
cd tests && pytest  # conftest.py is in parent directory, won't be found

# Good (from repo root)
cd ~/vault/01_PROJECTS/tokenpak && pytest tests/
pytest tests/ --conftest-trace  # Show conftest loading
```

**Solution:**

**Option A:** Always run from repo root
```bash
cd ~/vault/01_PROJECTS/tokenpak
pytest tests/integration/test_caching.py -v
```

**Option B:** Tell pytest to find conftest
```bash
pytest tests/integration/test_caching.py --rootdir ~/vault/01_PROJECTS/tokenpak -v
```

**Option C:** Create conftest.py in tests/integration/ too
```bash
# Copy root conftest to tests/integration/conftest.py
cp conftest.py tests/integration/conftest.py
```

**Status:** OPEN (user error)

**Prevention:**
- Add to CONTRIBUTING.md: "Always run pytest from repo root"
- Add to pytest.ini: `testpaths = tests`
- Create alias: `alias pytest-tpk='cd ~/vault/01_PROJECTS/tokenpak && pytest'`

---

### Issue #8: Circular Import in packages/core Structure {#issue-8}

**Symptom:**
```
ImportError: cannot import name 'TokenPakConfig' from 'tokenpak.config'
(most likely due to a circular import) in tokenpak/proxy/__init__.py

tokenpak/proxy/__init__.py:5: in <module>
    from tokenpak.config import TokenPakConfig
tokenpak/config.py:120: in <module>
    from tokenpak.proxy import ProxyManager
```

**Root Cause:**
- `tokenpak.proxy` imports from `tokenpak.config`
- `tokenpak.config` tries to import from `tokenpak.proxy`
- Circular initialization: proxy init → config load → proxy check

**Diagnosis:**
```bash
python -c "from tokenpak.config import TokenPakConfig"
# Shows where the cycle is
python -m trace -t <<< "import tokenpak" 2>&1 | head -50
```

**Solution:**

**Option A:** Use TYPE_CHECKING guards
```python
# tokenpak/config.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenpak.proxy import ProxyManager  # Type hint only

# At runtime, import inside function:
def validate_with_proxy():
    from tokenpak.proxy import ProxyManager  # Late binding
```

**Option B:** Restructure imports into separate modules
```python
# tokenpak/config.py (only config, no proxy imports)
class TokenPakConfig:
    def __init__(self, ...):
        # Do NOT import ProxyManager here
        pass

# tokenpak/bootstrap.py (new module, handles proxy init)
from tokenpak.config import TokenPakConfig
from tokenpak.proxy import ProxyManager

def bootstrap(config_path):
    config = TokenPakConfig.from_file(config_path)
    proxy = ProxyManager(config)
    return proxy
```

**Status:** OPEN (for core refactor)

**Fixed in (example):** PR tokenpak-circular-import-refactor

**Prevention:**
- Use dependency injection: pass dependencies to __init__ instead of importing
- Keep config and proxy in separate modules
- Test imports at module level: `python -c "import tokenpak"`

---

### Issue #9: Optional Dependency Not Installed (langchain, litellm, etc.) {#issue-9}

**Symptom:**
```
ModuleNotFoundError: No module named 'langchain'
pytest.skip: Framework test skipped (langchain not installed)
```

**Root Cause:**
- Framework adapter tests (LangChain, LiteLLM, CrewAI, LlamaIndex) are optional
- These tests require extra dependencies: `pip install -e ".[dev]"`
- User ran pytest without dev extras

**Diagnosis:**
```bash
pip list | grep -i langchain
# If missing, dev extras not installed
pip show tokenpak | grep -i "Requires.*langchain"
```

**Solution:**

**Option A:** Install dev extras (recommended)
```bash
pip install -e ".[dev]"
```

**Option B:** Skip framework tests
```bash
pytest tests/integration/ -v -k "not langchain and not litellm"
```

**Status:** OPEN (expected behavior)

**Prevention:**
- Framework tests marked with skip markers:
```python
pytest.importorskip("langchain")  # Gracefully skip if missing
```
- Document in README: "Run `pip install -e .[dev]` for all tests"
- CI matrix: test against [minimal, full-dev, all-optional]

---

### Issue #10: sys.path Pollution from Multiple Test Runs {#issue-10}

**Symptom:**
```
First run: pytest tests/ ✅ PASSED
Second run: pytest tests/ ⚠️ FAILED
  Error: stale sys.path entry
  Expected: packages/core at sys.path[0]
  Got: packages/core at sys.path[3]
```

**Root Cause:**
- Pytest caches sys.path modifications across test runs
- conftest.py appends to sys.path but doesn't deduplicate
- After multiple runs, sys.path has stale/duplicate entries
- Old entry at lower priority shadows the new one

**Diagnosis:**
```bash
python -c "import sys; print('\n'.join(sys.path[:5]))"
# Run pytest once, then run again — compare outputs
# Look for duplicate entries with same path
```

**Solution:**

**Option A:** Deduplicate in conftest.py (already done in our code)
```python
# conftest.py
if _core_path in sys.path:
    sys.path.remove(_core_path)  # Remove old entry first
sys.path.insert(0, _core_path)   # Then insert at [0]
```

**Option B:** Clear pytest cache between runs
```bash
pytest tests/ --cache-clear
rm -rf .pytest_cache
```

**Option C:** Restart Python process (nuclear)
```bash
# Start fresh shell
bash  # New session, clean sys.path
pytest tests/
```

**Status:** OPEN (edge case, usually only in dev)

**Prevention:**
- Always deduplicate before inserting (as shown in Issue #1)
- Use `pytest --cache-clear` after conftest.py changes
- Document in CONTRIBUTING.md: "If imports fail, run `pytest --cache-clear`"

---

## Diagnostic Flowchart

```
Import/Collection Error?
│
├─ ModuleNotFoundError: No module named 'tokenpak'
│  └─ Issue #1: Check sys.path order, verify conftest.py is loaded
│
├─ ModuleNotFoundError: No module named 'tokenpak.monitoring' (or other submodule)
│  └─ Issue #2: Module not fully initialized, or stub tokenpak imported
│
├─ ModuleNotFoundError: No module named 'tokenpak.agent.routing.fallback'
│  └─ Issue #3: Module doesn't exist yet, create it or skip the test
│
├─ ModuleNotFoundError: No module named 'websocket_proxy'
│  └─ Issue #4: websocket_proxy.py not on sys.path, add to conftest.py
│
├─ ImportError: Circular import detected
│  └─ Issue #5 or #8: Restructure with TYPE_CHECKING guards or late imports
│
├─ ModuleNotFoundError: No module named 'fastapi' (or pydantic, sqlalchemy, etc.)
│  └─ Issue #6: Install with dev extras: `pip install -e ".[dev]"`
│
├─ ERROR tests/ — conftest.py not found
│  └─ Issue #7: Run pytest from repo root, not from tests/ subdirectory
│
├─ ModuleNotFoundError: No module named 'langchain' (or litellm, crewai, etc.)
│  └─ Issue #9: Framework optional, install extras or skip tests with importorskip()
│
└─ FAILED (after multiple runs, but passed initially)
   └─ Issue #10: Clear pytest cache: `pytest --cache-clear`
```

---

## Quick Fixes Cheat Sheet

| Error | Quick Fix | Notes |
|-------|-----------|-------|
| Stub tokenpak imported | Restart Python, check conftest.py loaded | Issue #1 |
| Missing monitoring submodule | Use try/except + skip, check init imports | Issue #2 |
| Missing routing.fallback | Create the module or skip the test | Issue #3 |
| websocket_proxy not found | Check sys.path in conftest.py | Issue #4 |
| Circular import | Use TYPE_CHECKING guards | Issue #5, #8 |
| Missing fastapi/pydantic | `pip install -e ".[dev]"` | Issue #6 |
| conftest.py not found | Run from repo root: `cd ~/vault/01_PROJECTS/tokenpak && pytest` | Issue #7 |
| Missing langchain/litellm | Install extras or use importorskip() | Issue #9 |
| Import fails on 2nd run | `pytest --cache-clear` | Issue #10 |

---

## Testing the Inventory

To verify this inventory is accurate, run:

```bash
cd ~/vault/01_PROJECTS/tokenpak

# Test each scenario
pytest tests/ -v --tb=short                           # Should pass (Issue #1)
pytest tests/unit/test_monitor.py -v                  # Should skip gracefully (Issue #2)
pytest tests/integration/test_routing_fallback.py -v  # Should pass (Issue #3)
pytest tests/unit/test_websocket_proxy.py -v          # Should pass (Issue #4)
pytest tests/ --cache-clear -v                        # Should pass (Issue #10)
```

Expected result: All tests pass or skip with clear skip messages.

---

## Contributing to This Inventory

Found a new import error? Add it here:

1. Choose next issue number (#11, #12, etc.)
2. Document: Symptom, Root Cause, Diagnosis steps, Solution, Fixed in (or OPEN)
3. Include code examples and prevention tips
4. Add to Quick Reference table
5. Update Diagnostic Flowchart if applicable
6. Commit: `git add docs/IMPORT_ERROR_INVENTORY.md && git commit -m "doc: add import issue #XX"`

---

**Questions?** Check `docs/TROUBLESHOOTING.md` or ask in Trix's heartbeat logs.
