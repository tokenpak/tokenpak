"""Vault subsystem (Architecture §1).

The knowledge store: indexing, retrieval (keyword + semantic), chunking,
file parsers / AST / symbol extraction, filesystem watcher, SQLite
retrieval index. Level-1 primitive per §2.

Distinct from the maintainer-side Obsidian vault at ``~/vault/``;
the two share a name by coincidence and never share data.

Namespace init — actual code lives in subdirectories per the D1
migration roadmap.
"""

from __future__ import annotations
