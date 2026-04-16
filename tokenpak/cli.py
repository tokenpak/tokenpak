# SPDX-License-Identifier: Apache-2.0
"""DEPRECATED — this file is shadowed by the cli/ package and never loaded.

The canonical CLI implementation lives in:
  - tokenpak/_cli_core.py   (flat module, 7,699 LOC)
  - tokenpak/cli/            (package that re-exports _cli_core)

Python resolves `tokenpak.cli` to `cli/__init__.py` (the package), not
this file.  It is kept only to avoid confusing version-control diffs;
delete it when convenient.
"""

raise ImportError(
    "tokenpak/cli.py is shadowed by the cli/ package and should never be "
    "imported directly.  Use 'from tokenpak.cli import main' instead."
)
