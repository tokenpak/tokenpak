# SPDX-License-Identifier: Apache-2.0
"""Build compatible Python subprocess prefixes for companion launchers."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def python_spawn_prefix(
    *,
    executable: str | None = None,
    version_info: Sequence[int] | None = None,
) -> list[str]:
    """Return the interpreter argv prefix for a companion child process.

    Python added ``-P`` in 3.11. TokenPak supports Python 3.10, so every
    generated child command must gate the safe-path flag against the exact
    interpreter it will execute. Callers normally omit both arguments and use
    the running interpreter. Explicit arguments keep command construction
    deterministic in tests.

    Some embedded interpreters expose an empty ``sys.executable``. In that
    case, retain the historical ``python3`` fallback but omit ``-P`` because
    the fallback interpreter's version is unknown.
    """

    selected = sys.executable if executable is None else executable
    if not selected:
        return ["python3"]

    selected_version = sys.version_info if version_info is None else version_info
    prefix = [selected]
    if tuple(selected_version[:2]) >= (3, 11):
        prefix.append("-P")
    return prefix
