# Type Hints & IDE Support

TokenPak is being progressively typed with Python type hints to improve IDE autocomplete, catch type errors early, and provide better documentation.

## Current Type Coverage

**Status:** 1588 type errors (target: 0)

**Public API Coverage:** ~85% (all major entry points have type hints)

## Using TokenPak with Type Hints

### IDE Autocomplete

TokenPak's public APIs are fully typed. IDEs with Python language servers (PyCharm, VS Code + Pylance, etc.) provide full autocomplete:

```python
from tokenpak import Budgeter, CompressionEngine

budgeter = Budgeter()
# IDE shows all methods: allocate(), budget_report(), etc.
# All parameters and return types visible in tooltips

result = budgeter.allocate({...})
# IDE knows result is Dict[str, Any]
```

### Type Checking

Run mypy on your code:

```bash
# Check your project
mypy your_project/ --strict

# Check TokenPak internals (work in progress)
mypy ~/tokenpak/tokenpak/ --strict
```

### Type Hint Guidelines

When using TokenPak, provide types:

```python
from typing import Any, Dict
from tokenpak import Budgeter

components: Dict[str, Any] = {
    'state': {'text': '...', 'priority': 'critical'},
    'recent': {'text': '...', 'priority': 'high'},
    'evidence': {'items': [], 'priority': 'medium'},
    'tools': {'text': '...', 'priority': 'variable'},
}

budgeter = Budgeter()
trimmed: Dict[str, Any] = budgeter.allocate(components)
```

## Fixing Type Errors

### Common Issues

1. **Generic type parameters**: `list` → `list[str]`, `dict` → `dict[str, Any]`
2. **Missing return types**: `def func():` → `def func() -> str:`
3. **Missing parameter types**: `def func(x):` → `def func(x: str):`
4. **Optional defaults**: `def func(x: list = None):` → `def func(x: list[str] | None = None):`

### Per-Module Status

| Module | Status | Notes |
|--------|--------|-------|
| budgeter.py | ✅ Public API typed | 6 internal errors (Any returns) |
| registry.py | ⚠️ Partial | Base public API typed |
| telemetry/ | ⚠️ Partial | Main interfaces need work |
| engines/ | ⚠️ Partial | Base classes typed, implementations pending |
| connectors/ | ❌ Minimal | 15+ errors per module |
| cli/ | ❌ Minimal | ~20 untyped functions |

## Contributing

When adding new code:

1. Add type hints to all function signatures (parameters + return types)
2. Use `from typing import Any, Dict, List, Optional, Union, etc.`
3. Run `mypy --strict` locally before committing
4. For hard-to-type code, create `.pyi` stub files

## References

- [Python Type Hints (PEP 484)](https://peps.python.org/pep-0484/)
- [Mypy Documentation](https://mypy.readthedocs.io/)
- [Typing Module](https://docs.python.org/3/library/typing.html)
