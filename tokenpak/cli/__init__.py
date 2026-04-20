# SPDX-License-Identifier: Apache-2.0
"""Public CLI package for TokenPak.

Exposes `main` so the `tokenpak=tokenpak.cli:main` console entry point and
`python -m tokenpak` (via `tokenpak/__main__.py`) both resolve correctly.

Implementation lives in `tokenpak.cli._impl`; this module is a thin surface
that also re-exports the `commands` subpackage for consumers that want it.
"""

from tokenpak.cli import commands as commands
from tokenpak.cli._impl import main

# Per Architecture §2.4, entrypoints reach the execution backbone
# through tokenpak.proxy.client. Made available here so command
# modules can dispatch via `from tokenpak.cli import proxy_client`
# rather than crossing subsystem boundaries per-command. The import
# itself is trivial; in-process transport is active when services.
# execute has a dispatcher wired (post-D1 code consolidation).
from tokenpak import proxy as _tp_proxy  # noqa: F401
from tokenpak.proxy import client as proxy_client  # noqa: F401

__all__ = ["main", "commands", "proxy_client"]
