"""Compatibility shim — tokenpak.agentic.* re-exports from tokenpak.orchestration.*

This package does not contain any code of its own.  Every submodule reference
(``from tokenpak.agentic.workflow import WorkflowManager``, etc.) is resolved
by installing a custom meta-path finder that redirects all ``tokenpak.agentic.X``
imports to ``tokenpak.orchestration.X``.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys


class _AgenticRedirectFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that redirects tokenpak.agentic.* -> tokenpak.orchestration.*"""

    _PREFIX = "tokenpak.agentic."

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(self._PREFIX):
            return None

        # Compute the target module in the orchestration namespace
        suffix = fullname[len(self._PREFIX):]
        target_name = f"tokenpak.orchestration.{suffix}"

        return importlib.machinery.ModuleSpec(
            fullname,
            _AgenticRedirectLoader(target_name),
        )


class _AgenticRedirectLoader(importlib.abc.Loader):
    """Loader that imports from tokenpak.orchestration and aliases into tokenpak.agentic."""

    def __init__(self, target_name):
        self._target_name = target_name

    def create_module(self, spec):
        return None  # Use default semantics

    def exec_module(self, module):
        # Import the real module from orchestration
        real = importlib.import_module(self._target_name)
        # Replace the module in sys.modules with the real one
        sys.modules[module.__name__] = real
        # Copy all attributes (so the caller's module object works)
        module.__dict__.update(real.__dict__)
        module.__path__ = getattr(real, '__path__', [])
        module.__file__ = getattr(real, '__file__', None)
        module.__spec__ = real.__spec__


# Install the finder once at import time
if not any(isinstance(f, _AgenticRedirectFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AgenticRedirectFinder())


# Also support attribute access on the package itself (e.g.
# ``import tokenpak.agentic; tokenpak.agentic.HandoffManager``).
_CLASS_MAP = {
    "ErrorNormalizer": "tokenpak.orchestration.error_normalizer",
    "RetryEngine": "tokenpak.orchestration.retry",
    "HandoffManager": "tokenpak.orchestration.handoff",
    "HandoffBlock": "tokenpak.orchestration.handoff",
    "HandoffStatus": "tokenpak.orchestration.handoff",
    "HandoffWire": "tokenpak.orchestration.handoff",
    "TokenPak": "tokenpak.orchestration.handoff",
    "ContextRef": "tokenpak.orchestration.handoff",
}


def __getattr__(name: str):
    # Submodule access
    try:
        mod = importlib.import_module(f"tokenpak.orchestration.{name}")
        setattr(sys.modules[__name__], name, mod)
        sys.modules[f"tokenpak.agentic.{name}"] = mod
        return mod
    except ImportError:
        pass

    # Class-level access
    if name in _CLASS_MAP:
        parent = importlib.import_module(_CLASS_MAP[name])
        obj = getattr(parent, name)
        setattr(sys.modules[__name__], name, obj)
        return obj

    raise AttributeError(f"module 'tokenpak.agentic' has no attribute {name!r}")
