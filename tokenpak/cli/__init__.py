# SPDX-License-Identifier: Apache-2.0
"""Public CLI package for TokenPak.

Exposes `main` so the `tokenpak=tokenpak.cli:main` console entry point and
`python -m tokenpak` (via `tokenpak/__main__.py`) both resolve correctly.

Implementation lives in `tokenpak.cli._impl`; this module is a thin surface
that also re-exports the `commands` subpackage for consumers that want it.
"""

from tokenpak.cli._impl import main
from tokenpak.cli import commands as commands

__all__ = ["main", "commands"]
