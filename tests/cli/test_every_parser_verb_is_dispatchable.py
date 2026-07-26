# SPDX-License-Identifier: Apache-2.0
"""A verb the parser accepts must be runnable.

`main()` screens argv against a verb set before argparse sees it, and that set
was two hand-maintained lists. Two verbs the parser registers — `upgrade` and
`telemetry` — were absent from them, so running either produced:

    ❌ Unknown command: 'upgrade'
       Did you mean: tokenpak update?

while the command sat registered and dispatchable a few frames away. For
`upgrade` that silently voided the compatibility shim it exists to be, and
sent the user to an unrelated verb instead.

The pairing is the point: any future verb added to the parser is dispatchable
without anyone remembering to update a second list.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak._cli_core import _core_command_names, registered_command_names

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_parser_verb_is_rejected_as_unknown() -> None:
    blocked = sorted(registered_command_names() - _core_command_names())
    assert not blocked, (
        f"the parser accepts {blocked} but main() rejects them as unknown commands "
        "before argparse runs"
    )


def test_the_verb_set_is_not_trivially_empty() -> None:
    """Guard the guard: an empty parser set would satisfy the check above."""
    assert len(registered_command_names()) > 50


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
        "TOKENPAK_PORT": "8899",
    }
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    tpk = tmp_path / ".tpk"
    tpk.mkdir(parents=True)
    (tpk / ".seen_intro").touch()
    return tmp_path


def test_upgrade_is_a_working_shim_that_states_enrollment_is_unavailable(home: Path) -> None:
    result = _run(home, "upgrade")
    out = result.stdout + result.stderr

    assert "Unknown command" not in out, f"the shim is unreachable:\n{out}"
    assert result.returncode == 0, f"the shim should succeed, got {result.returncode}:\n{out}"
    assert "not available" in out.lower(), (
        f"the shim must say public Pro enrollment is unavailable:\n{out}"
    )
    # Ruling: it must not open a browser, and must not advertise a destination.
    assert "http://" not in out and "https://" not in out, (
        f"the shim must not offer an enrollment URL:\n{out}"
    )


def test_upgrade_stays_out_of_default_discovery(home: Path) -> None:
    """Hidden, not deleted: absent from `help`, present under `help --all`."""
    assert "upgrade" not in _run(home, "help").stdout
    assert "upgrade" in _run(home, "help", "--all").stdout
