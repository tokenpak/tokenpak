"""savings command — DEPRECATED. Use `tokenpak status` instead.

`tokenpak savings` is a legacy command. All savings data now appears in the
default `tokenpak status` output (savings-first layout, v3).

This wrapper prints a deprecation notice and then delegates to `tokenpak status`
with equivalent flags, so existing scripts and habits keep working.

Flag mapping:
    tokenpak savings              → tokenpak status
    tokenpak savings --verbose    → tokenpak status --full
    tokenpak savings --json       → tokenpak status --json
    tokenpak savings --period Xd  → (period ignored; status uses live DB)
"""

from __future__ import annotations

import sys
from typing import Optional

_DEPRECATION_NOTICE = """\
⚠️  `tokenpak savings` is deprecated.
    All savings data is now shown in `tokenpak status` (default view).
    Please update your workflow: tokenpak status
"""


def run_savings_cmd(args) -> None:
    """Dispatch handler for 'tokenpak savings' — prints notice then delegates to status."""
    print(_DEPRECATION_NOTICE)

    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False) or getattr(args, "as_json", False)

    try:
        from tokenpak.cli.commands.status import run, run_full, _run_json

        if as_json:
            _run_json()
        elif verbose:
            run_full()
        else:
            run()
    except ImportError as exc:  # pragma: no cover
        print(f"  (Could not load status command: {exc})", file=sys.stderr)


# ---------------------------------------------------------------------------
# Click entrypoint (if available)
# ---------------------------------------------------------------------------

try:
    import click

    @click.command("savings")
    @click.option("--verbose", "-v", is_flag=True, help="[deprecated] Use tokenpak status --full")
    @click.option("--json", "as_json", is_flag=True, help="[deprecated] Use tokenpak status --json")
    @click.option("--period", default="24h", hidden=True, help="[deprecated] Ignored")
    def savings_cmd(verbose: bool, as_json: bool, period: str) -> None:
        """[DEPRECATED] Use `tokenpak status` instead.

        All savings data is now shown in the default `tokenpak status` output.
        """

        class _Args:
            pass

        a = _Args()
        a.verbose = verbose
        a.json = as_json
        a.as_json = as_json
        a.period = period
        run_savings_cmd(a)

except ImportError:
    savings_cmd = None  # type: ignore[assignment]
