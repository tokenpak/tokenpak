# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the 3 blockers an independent smoke pass surfaced.

An independent smoke pass (2026-05-15) found:

1. ``tokenpak activate`` ignored ``TOKENPAK_HOME`` and wrote
   ``license.json`` to the host's real ``~/.tokenpak/`` instead of
   under the resolved Std 33 home. Sandbox-escape + boundary
   violation in one.

2. ``tokenpak pak create`` printed ``✗ source directory not found``
   to stderr but exited 0, breaking ``set -e`` scripts.

3. ``tokenpak pak import`` (duplicate without ``--force``) printed
   ``✗ already installed`` to stderr but exited 0.

These tests prevent regression on each.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Blocker 1: license_path honors Std 33
# ---------------------------------------------------------------------------


def test_license_path_honors_TOKENPAK_HOME(tmp_path, monkeypatch):
    """activate() must write under <TOKENPAK_HOME>/license.json, NOT host ~/.tokenpak/."""
    from tokenpak import licensing as _lic

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "sandbox"))
    monkeypatch.delenv("TOKENPAK_LICENSE_FILE", raising=False)

    resolved = _lic._license_path()
    assert resolved == tmp_path / "sandbox" / "license.json", (
        f"license path escaped TOKENPAK_HOME: got {resolved}"
    )


def test_activate_writes_under_TOKENPAK_HOME(tmp_path, monkeypatch):
    """Sandbox-escape regression: activate must not touch host ~/.tokenpak/."""
    from tokenpak import licensing as _lic

    home = tmp_path / "sandbox"
    monkeypatch.setenv("TOKENPAK_HOME", str(home))
    monkeypatch.delenv("TOKENPAK_LICENSE_FILE", raising=False)

    # Force daemon probe to "unavailable" so activate doesn't try to
    # consult an unrelated daemon during the test.
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "unavailable",
    )

    result = _lic.activate("BOUNDARY-FIX-LICENSE-KEY-0001")
    assert result.ok is True

    # The license MUST be under the sandbox, not under the host home.
    assert (home / "license.json").exists(), (
        f"license.json was not written under TOKENPAK_HOME ({home})"
    )

    # And the host's real ~/.tokenpak/ MUST NOT have been touched by
    # this test (we can't assert this directly without risking damage
    # if the test runs against a real install — but we can confirm
    # the resolver doesn't return the host path).
    assert _lic._license_path() == home / "license.json"


def test_TOKENPAK_LICENSE_FILE_explicit_override_still_works(tmp_path, monkeypatch):
    """The legacy explicit override path must still work."""
    from tokenpak import licensing as _lic

    custom = tmp_path / "explicit.json"
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(custom))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "ignored-when-override-set"))
    assert _lic._license_path() == custom


# ---------------------------------------------------------------------------
# Blockers 2 + 3: pak create / pak import exit codes via the real dispatcher
# ---------------------------------------------------------------------------


def _run_cli(args, env_extra=None):
    """Invoke the real CLI via subprocess so we hit the dispatcher's
    return-code handling, not the in-process handler return value."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tokenpak", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_pak_create_missing_source_exits_nonzero(tmp_path):
    """Blocker: pak create on missing src printed error but exited 0."""
    out = tmp_path / "out.pak.json"
    result = _run_cli(
        ["pak", "create", str(tmp_path / "nope"), "-o", str(out)],
        env_extra={"TOKENPAK_HOME": str(tmp_path / "home")},
    )
    assert result.returncode != 0, (
        f"pak create on missing source exited 0 (stderr: {result.stderr!r})"
    )
    assert "not found" in (result.stderr + result.stdout).lower()


def test_pak_import_missing_file_exits_nonzero(tmp_path):
    """Blocker: pak import of missing file printed error but exited 0."""
    result = _run_cli(
        ["pak", "import", str(tmp_path / "nope.pak.json")],
        env_extra={"TOKENPAK_HOME": str(tmp_path / "home")},
    )
    assert result.returncode != 0, (
        f"pak import of missing file exited 0 (stderr: {result.stderr!r})"
    )
    assert "not found" in (result.stderr + result.stdout).lower()


def test_pak_import_duplicate_exits_nonzero(tmp_path):
    """Blocker: duplicate pak import without --force exited 0."""
    home = tmp_path / "home"
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello\n")
    pak_file = tmp_path / "p.pak.json"

    # Create + import once (must succeed)
    cre = _run_cli(
        ["pak", "create", str(src), "-o", str(pak_file)],
        env_extra={"TOKENPAK_HOME": str(home)},
    )
    assert cre.returncode == 0, cre.stderr

    imp1 = _run_cli(
        ["pak", "import", str(pak_file)],
        env_extra={"TOKENPAK_HOME": str(home)},
    )
    assert imp1.returncode == 0, imp1.stderr

    # Second import without --force MUST exit non-zero
    imp2 = _run_cli(
        ["pak", "import", str(pak_file)],
        env_extra={"TOKENPAK_HOME": str(home)},
    )
    assert imp2.returncode != 0, f"duplicate pak import exited 0 (stderr: {imp2.stderr!r})"
    assert "already installed" in (imp2.stderr + imp2.stdout).lower()
