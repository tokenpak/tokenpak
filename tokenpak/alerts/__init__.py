"""tokenpak.alerts — backward-compat shim.

Implementation has moved to tokenpak._internal.alerts.
This shim re-exports all names (including private) for backward compatibility.
"""
import sys as _sys
import importlib as _importlib

_mod = _importlib.import_module("tokenpak._internal.alerts")
_sys.modules[__name__].__dict__.update(
    {k: v for k, v in _mod.__dict__.items() if not (k.startswith("__") and k.endswith("__"))}
)

def __getattr__(name: str):
    return getattr(_mod, name)
