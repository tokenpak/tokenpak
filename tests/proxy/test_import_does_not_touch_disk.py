# SPDX-License-Identifier: Apache-2.0
"""Importing a module must not create state.

The watchdog resolved four paths through the *read* resolver at module scope
and then created a directory and opened a log file next to them — all at
import. On a machine with a leftover empty legacy home that wrote into the
legacy directory, and because home resolution keys on which directory holds
state, that single import handed the whole installation to the legacy path.

Read resolution decides where to look. It must never decide where to write,
and importing a module must not do either.

These run the import in a subprocess so the check is real: an in-process
import may already have happened via another test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_MODULES = [
    "tokenpak.proxy.proxy_watchdog",
    "tokenpak.proxy.config",
    "tokenpak.core.cooldown",
    "tokenpak.core.auth.oauth_manager",
    "tokenpak.licensing",
]


def _import_in_subprocess(home: Path, module: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        timeout=180,
    )


@pytest.mark.parametrize("module", _MODULES)
def test_import_creates_no_home(tmp_path: Path, module: str) -> None:
    """A bare import on a clean machine must leave the disk alone."""
    result = _import_in_subprocess(tmp_path, module)
    assert result.returncode == 0, f"{module} failed to import:\n{result.stderr}"

    created = sorted(p.name for p in tmp_path.iterdir())
    assert created == [], f"importing {module} created {created}"


@pytest.mark.parametrize("module", _MODULES)
def test_import_writes_nothing_into_a_leftover_legacy_home(tmp_path: Path, module: str) -> None:
    """The capture scenario: an empty ~/.tokenpak left behind by an old install."""
    legacy = tmp_path / ".tokenpak"
    legacy.mkdir()

    result = _import_in_subprocess(tmp_path, module)
    assert result.returncode == 0, f"{module} failed to import:\n{result.stderr}"

    wrote = sorted(p.name for p in legacy.iterdir())
    assert wrote == [], f"importing {module} wrote {wrote} into the legacy home"
