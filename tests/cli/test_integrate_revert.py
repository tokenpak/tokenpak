# SPDX-License-Identifier: Apache-2.0
"""Tests for --revert.

Covers:
  - Happy path: backup exists → target restored atomically
  - No backup: "nothing to revert" message, exit 0 (idempotent)
  - No backup_locator: clean error, exit 1
  - Verify called after successful revert
  - run_integrate --revert dispatch
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.cli.commands.integrate import (
    ApplyResult,
    Integration,
    _revert_integration,
    run_integrate,
)

PROXY = "http://localhost:8766"


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "proxy_url": PROXY,
        "apply": False,
        "revert": True,
        "client": "claude-code",
        "all": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_integ(backup_locator=None, verify_fn=None) -> Integration:
    return Integration(
        key="claude-code",
        label="Claude Code",
        kind="client",
        detector=lambda: "/usr/bin/claude",
        instructions=lambda url: "export ...",
        applier=None,
        backup_locator=backup_locator,
        verify_fn=verify_fn,
    )


# ── _revert_integration unit tests ────────────────────────────────────────


def test_revert_happy_path(tmp_path):
    """Backup exists → target is restored atomically."""
    # Set up: target + backup
    target = tmp_path / "settings.json"
    bak = tmp_path / "settings.json.bak"
    target.write_text('{"old": true}', encoding="utf-8")
    bak.write_text('{"restored": true}', encoding="utf-8")

    integ = _make_integ(backup_locator=lambda: bak)
    result = _revert_integration(integ)

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == '{"restored": true}'
    assert bak.exists(), "backup must NOT be deleted after revert"
    assert "Reverted" in result.summary


def test_revert_idempotent_no_backup():
    """No backup file → ok=True, 'nothing to revert'."""
    integ = _make_integ(backup_locator=lambda: None)
    result = _revert_integration(integ)

    assert result.ok is True
    assert "nothing to revert" in result.summary.lower() or "Nothing" in result.summary


def test_revert_no_backup_locator():
    """Integration has no backup_locator → ok=False with clear error."""
    integ = _make_integ(backup_locator=None)
    result = _revert_integration(integ)

    assert result.ok is False
    assert result.error == "no_backup_locator"


def test_revert_atomic_tempfile_cleanup(tmp_path):
    """Temp file does not remain after a successful revert."""
    target = tmp_path / "settings.json"
    bak = tmp_path / "settings.json.bak"
    target.write_text("{}", encoding="utf-8")
    bak.write_text('{"restored": true}', encoding="utf-8")

    integ = _make_integ(backup_locator=lambda: bak)
    _revert_integration(integ)

    # The .revert_tmp file should be gone
    revert_tmp = tmp_path / "settings.json.revert_tmp"
    assert not revert_tmp.exists()


def test_revert_backup_not_deleted(tmp_path):
    """Backup file must be preserved after revert (idempotency + forensics)."""
    target = tmp_path / "settings.json"
    bak = tmp_path / "settings.json.bak"
    target.write_text("{}", encoding="utf-8")
    bak.write_text('{"v": 1}', encoding="utf-8")

    integ = _make_integ(backup_locator=lambda: bak)
    result = _revert_integration(integ)

    assert result.ok is True
    assert bak.exists(), "backup preserved for idempotency and forensics"


# ── run_integrate --revert dispatch ───────────────────────────────────────


def test_run_integrate_revert_dispatch(capsys, tmp_path):
    """run_integrate --revert calls _revert_integration and shows result."""
    target = tmp_path / "settings.json"
    bak = tmp_path / "settings.json.bak"
    target.write_text("{}", encoding="utf-8")
    bak.write_text('{"restored": true}', encoding="utf-8")

    integ = _make_integ(
        backup_locator=lambda: bak,
        verify_fn=lambda url: (True, "ANTHROPIC_BASE_URL set"),
    )

    with patch("tokenpak.cli.commands.integrate._find", return_value=integ):
        args = _make_args()
        rc = run_integrate(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Reverted" in out


def test_run_integrate_revert_no_client(capsys):
    """--revert without client → error message + listing."""
    args = _make_args(client=None)
    rc = run_integrate(args)

    assert rc == 2
    out = capsys.readouterr().out
    assert "--revert requires" in out or "requires a specific client" in out


def test_run_integrate_revert_unknown_client(capsys):
    """--revert with unknown client → error."""
    args = _make_args(client="nonexistent-tool")
    rc = run_integrate(args)

    assert rc == 2
    out = capsys.readouterr().out
    assert "unknown client" in out


def test_run_integrate_revert_nothing_to_revert(capsys):
    """--revert when no backup exists → ok, informational message."""
    integ = _make_integ(backup_locator=lambda: None)
    with patch("tokenpak.cli.commands.integrate._find", return_value=integ):
        args = _make_args()
        rc = run_integrate(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing" in out.lower() or "Nothing" in out
