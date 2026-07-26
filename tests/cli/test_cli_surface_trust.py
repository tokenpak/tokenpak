# SPDX-License-Identifier: Apache-2.0
"""CLI surface/trust regression tests (help-contract).

Ratified curated-1.9.4 item: a regression test that the help contract is honest —
- an unknown verb exits nonzero (not 0),
- the unknown-command error goes to stderr,
- ``tokenpak help`` advertises a *truthful* command count (matches the live registry).

Offline tests (no network).
"""

from __future__ import annotations

import re
import sys
from io import StringIO
from unittest.mock import patch

import pytest


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI dispatcher with argv; return (exit_code, stdout, stderr)."""
    from tokenpak.cli import main

    out, err = StringIO(), StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        with patch.object(sys, "argv", ["tokenpak"] + argv):
            try:
                main()
                code = 0
            except SystemExit as exc:
                code = int(exc.code) if exc.code is not None else 0
    return code, out.getvalue(), err.getvalue()


def test_unknown_command_exits_nonzero():
    """An unrecognised verb must exit nonzero, not 0."""
    code, _, _ = _run(["zzz-not-a-real-command"])
    assert code != 0


def test_unknown_command_prints_to_stderr():
    """The unknown-command error goes to stderr, not stdout."""
    _, out, err = _run(["totally-made-up-verb"])
    assert "Unknown command" in err
    assert "Unknown command" not in out


def test_help_count_is_truthful():
    """The advertised count must equal the surface ``--all`` actually supports.

    The count used to be measured against the whole registry. It is now
    measured against the *supported* surface, because that is what the
    sentence claims — and a number that counts something other than what the
    words say is the defect this test exists to catch, in either direction.
    """
    from tokenpak.cli.commands import help as help_mod

    code, out, err = _run(["help"])
    assert code == 0
    text = out + err
    m = re.search(r"all\s+(\d+)\s+supported commands", text)
    assert m, "help must advertise an 'all N supported commands' count"
    advertised = int(m.group(1))
    actual = len(help_mod.discoverable_commands())
    assert advertised == actual, f"help advertises {advertised} but {actual} are supported"


def test_help_all_lists_every_supported_command():
    """Every command the count promises must actually appear under --all."""
    from tokenpak.cli.commands import help as help_mod

    code, out, err = _run(["help", "--all"])
    assert code == 0
    text = out + err
    missing = [
        cmd["command"] for cmd in help_mod.discoverable_commands() if cmd["command"] not in text
    ]
    assert not missing, f"advertised as supported but absent from --all: {missing}"


def test_help_all_labels_unsupported_commands():
    """Commands outside the surface must be labelled, not silently mixed in."""
    code, out, err = _run(["help", "--all"])
    assert code == 0
    text = out + err
    assert "Outside the supported surface" in text


@pytest.mark.xfail(
    reason=(
        "pre-existing 1.9.3 behavior: `tokenpak <unknown> --help` exits 0 instead of "
        "nonzero. The fix lives in the later cli/_impl refactor (9831050ba1) which is "
        "out of scope for the v1.10.0 Dispatch-graduation release; tracked as a residual."
    ),
    strict=False,
)
def test_unknown_command_help_flag_exits_nonzero():
    """`tokenpak <unknown> --help` should exit nonzero (currently rc0 — known residual)."""
    code, _, _ = _run(["zzz-not-a-real-command", "--help"])
    assert code != 0
