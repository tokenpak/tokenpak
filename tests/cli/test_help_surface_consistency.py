# SPDX-License-Identifier: Apache-2.0
"""CLI surface consistency test (CALI-MTC-01).

Asserts that every command shown in `tokenpak --help` is actually registered
in the argparse parser (exits 0 on `<cmd> --help`).  This prevents phantom
commands from appearing in the help text without a working implementation.
"""

from __future__ import annotations

import re
import subprocess
import sys


def _parse_command_list(help_output: str) -> list[str]:
    """Extract command names from `tokenpak --help` output.

    Looks for lines of the form:
        "  <cmd>  <description>"
    where <cmd> is a single token (no spaces, no leading dash).
    """
    commands = []
    for line in help_output.splitlines():
        # Lines like "  start        Start the proxy ..."
        m = re.match(r"^\s{1,4}([a-z][a-z0-9-]+)\s{2,}", line)
        if m:
            commands.append(m.group(1))
    return commands


def test_help_surface_consistency():
    """Every command listed in `tokenpak --help` must exit 0 on `<cmd> --help`."""
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"`tokenpak --help` failed:\n{result.stderr}"

    commands = _parse_command_list(result.stdout)
    assert commands, "No commands parsed from `tokenpak --help` output"

    failures = []
    for cmd in commands:
        sub = subprocess.run(
            [sys.executable, "-m", "tokenpak", cmd, "--help"],
            capture_output=True,
            text=True,
        )
        if sub.returncode != 0:
            failures.append(f"  phantom: tokenpak {cmd!r} (exit {sub.returncode})")

    assert not failures, "Phantom commands detected in `tokenpak --help`:\n" + "\n".join(failures)
