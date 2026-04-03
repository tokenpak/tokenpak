"""
tokenpak.agent — Backward-compatibility shim.

All submodules were moved to canonical top-level paths in tokenpak 1.0.3.
This shim allows existing code importing from ``tokenpak.agent.*`` to keep
working through tokenpak 1.0.x with a deprecation warning.

**Removed in tokenpak 1.1.0.**  Update your imports:

    # OLD (deprecated)
    from tokenpak.agent.compression import CompressionPipeline

    # NEW (canonical)
    from tokenpak.compression import CompressionPipeline
"""

import warnings
import importlib

# Maps old agent.X names to their new canonical module paths.
_REDIRECTS = {
    # Top-level modules (moved out of agent/)
    "adapters": "tokenpak.adapters",
    "agentic": "tokenpak.agentic",
    "cli": "tokenpak.cli",
    "dashboard": "tokenpak.dashboard",
    "proxy": "tokenpak.proxy",
    "routing": "tokenpak.routing",
    "semantic": "tokenpak.semantic",
    "telemetry": "tokenpak.telemetry",
    "vault": "tokenpak.vault",
    # _internal modules (moved into tokenpak._internal/)
    "auth": "tokenpak._internal.auth",
    "debug": "tokenpak._internal.debug",
    "fingerprint": "tokenpak._internal.fingerprint",
    "ingest": "tokenpak._internal.ingest",
    "macros": "tokenpak._internal.macros",
    "memory": "tokenpak._internal.memory",
    "query": "tokenpak._internal.query",
    "regression": "tokenpak._internal.regression",
    "state_schemas": "tokenpak._internal.state_schemas",
    "teacher": "tokenpak._internal.teacher",
    "team": "tokenpak._internal.team",
    "triggers": "tokenpak._internal.triggers",
}


def __getattr__(name: str):
    """Intercept attribute access and redirect to canonical paths."""
    if name in _REDIRECTS:
        new_path = _REDIRECTS[name]
        warnings.warn(
            f"'tokenpak.agent.{name}' is deprecated. "
            f"Use '{new_path}' instead. "
            f"Will be removed in tokenpak 1.1.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(new_path)
    raise AttributeError(f"module 'tokenpak.agent' has no attribute {name!r}")
