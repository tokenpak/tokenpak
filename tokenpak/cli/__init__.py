# SPDX-License-Identifier: Apache-2.0
"""Public CLI package for TokenPak.

Exposes `main` so the `tokenpak=tokenpak.cli:main` console entry point and
`python -m tokenpak` (via `tokenpak/__main__.py`) both resolve correctly.

Implementation lives in `tokenpak.cli._impl`; this module is a thin surface
that also re-exports the `commands` subpackage for consumers that want it.

Two-dispatcher pattern (intentional, per DECISIONS-AC-05.md):

  - ``tokenpak.cli._impl.main`` — the full argparse-based CLI dispatcher
    for all 93 commands. Re-exported here and invoked by the ``tokenpak``
    console script.

  - ``tokenpak.cli.main.main`` — specialized fast-path for heavy daemon
    subcommands (``serve``, ``trigger``, ``workflow``) that bypass the
    full argparse init for startup-latency reasons. Exposed separately
    via the ``tokenpak-main`` console script.

Do not "clean up" to a single dispatcher — both are exercised by
integration tests; collapsing would regress ``tokenpak serve`` startup.
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
