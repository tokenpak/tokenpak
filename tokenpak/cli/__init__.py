# SPDX-License-Identifier: MIT
"""TokenPak CLI package — compatibility shim.

Re-exports everything from _cli_core so that `import tokenpak.cli as cli`
preserves the same API as when cli.py was a flat module.
"""
import importlib as _importlib
import sys as _sys
import types as _types

# Re-export the underlying module directly so that `tokenpak.cli` IS _cli_core
# (attributes, private names, constants all work as expected by existing tests)
_core = _importlib.import_module("tokenpak._cli_core")

# Copy all attributes onto this package
_this_module = _sys.modules[__name__]
for _name, _val in vars(_core).items():
    setattr(_this_module, _name, _val)

# Ensure the key public names are importable
main = _core.main  # noqa: F811
