# SPDX-License-Identifier: Apache-2.0
"""TokenPak CLI package — compatibility shim.

Re-exports everything from _cli_core so that `import tokenpak.cli as cli`
preserves the same API as when cli.py was a flat module.
"""

import importlib as _importlib
import sys as _sys
import types as _types  # noqa: F401 — used by package-level API contract

# Re-export the underlying module directly so that `tokenpak.cli` IS _cli_core
# (attributes, private names, constants all work as expected by existing tests)
_core = _importlib.import_module("tokenpak._cli_core")

# Copy all non-dunder attributes onto this package.
# Skipping dunders (e.g. __name__, __file__, __spec__, __loader__) is critical:
# copying them from the flat module onto the package overwrites loader metadata
# and causes "loader cannot handle" errors when invoked via -m tokenpak.cli.
_this_module = _sys.modules[__name__]
for _name, _val in vars(_core).items():
    if not (_name.startswith("__") and _name.endswith("__")):
        setattr(_this_module, _name, _val)

# Ensure the key public names are importable
main = _core.main  # noqa: F811
