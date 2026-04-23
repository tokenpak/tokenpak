# SPDX-License-Identifier: Apache-2.0
"""A1 (PM/GTM v2 Phase 0): verify `tokenpak setup` is registered and discoverable.

Preflight 2026-04-23 observed that `cmd_setup` was dispatch-registered via
argparse but absent from `_COMMAND_GROUPS`, so `tokenpak help` did not
advertise it. README claim "zero config" relied on this hidden wizard.

These smokes assert the fix holds:
  1. `setup` appears in `_COMMAND_GROUPS["Getting Started"]`.
  2. `tokenpak setup --help` exits zero and mentions the wizard.
  3. The argparse subparser for `setup` is still wired to `cmd_setup`.
"""

from __future__ import annotations

import subprocess
import sys


def test_setup_listed_in_getting_started_group():
    """A1: setup must appear in the Getting Started group so `tokenpak help` shows it."""
    from tokenpak.cli._impl import _COMMAND_GROUPS

    getting_started = dict(_COMMAND_GROUPS["Getting Started"])
    assert "setup" in getting_started, (
        "`setup` must be listed in _COMMAND_GROUPS['Getting Started']. "
        "Without it, `tokenpak help` hides the wizard and README's "
        "'one command to configure' claim is not discoverable."
    )
    help_text = getting_started["setup"]
    assert "wizard" in help_text.lower() or "interactive" in help_text.lower(), (
        f"`setup` help-group text should describe the wizard: got {help_text!r}"
    )


def test_setup_subparser_dispatches_to_cmd_setup():
    """A1: argparse dispatch for `setup` must resolve to cmd_setup."""
    from tokenpak.cli._impl import build_parser, cmd_setup

    parser = build_parser()
    ns = parser.parse_args(["setup"])
    assert ns.func is cmd_setup, (
        f"Expected `tokenpak setup` to dispatch to cmd_setup; got {ns.func!r}"
    )


def test_tokenpak_setup_help_exits_zero():
    """A1: `tokenpak setup --help` should exit cleanly and advertise the wizard."""
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "setup", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"`tokenpak setup --help` must exit zero; got {result.returncode}.\n"
        f"stderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "wizard" in combined or "interactive" in combined or "configuration" in combined, (
        "`tokenpak setup --help` output should reference the wizard/interactive/configuration. "
        f"Got:\n{result.stdout}\n{result.stderr}"
    )
