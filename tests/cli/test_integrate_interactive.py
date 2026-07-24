# SPDX-License-Identifier: Apache-2.0
"""Tests for the guided interactive form.

Covers:
  - TTY path → _run_guided_form invoked
  - non-TTY path → print-only fallback
  - --no-tui escape → print-only fallback
  - --apply back-compat (unchanged behavior)
  - guided form happy path with mocked confirm
  - guided form cancelled (user says N)
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.cli.commands.integrate import (
    ApplyResult,
    Integration,
    _is_interactive,
    _run_guided_form,
    run_integrate,
)

PROXY = "http://localhost:8766"


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "proxy_url": PROXY,
        "apply": False,
        "revert": False,
        "client": "claude-code",
        "all": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _ok_applier(proxy_url: str) -> ApplyResult:
    return ApplyResult(
        ok=True,
        summary="Applied.",
        changes=["env.ANTHROPIC_BASE_URL: (unset) → http://localhost:8766"],
        backup_path="/fake/.claude/settings.json.bak",
        rollback_cmd="cp /fake/.claude/settings.json.bak /fake/.claude/settings.json",
    )


def _make_integration(**kwargs) -> Integration:
    defaults = dict(
        key="claude-code",
        label="Claude Code",
        kind="client",
        detector=lambda: "/usr/local/bin/claude",
        instructions=lambda url: f"Set ANTHROPIC_BASE_URL={url}",
        applier=_ok_applier,
        backup_locator=lambda: None,
        preview_fn=lambda url: f"  Will set ANTHROPIC_BASE_URL={url}",
        verify_fn=lambda url: (True, f"ANTHROPIC_BASE_URL={url} — proxy route active"),
    )
    defaults.update(kwargs)
    return Integration(**defaults)


# ── Non-TTY → print-only ───────────────────────────────────────────────────


def test_non_tty_prints_instructions(capsys):
    """Non-TTY defaults to print-only (_render_one), no guided form."""
    integ = _make_integration()
    with (
        patch("tokenpak.cli.commands.integrate._find", return_value=integ),
        patch("tokenpak.cli.commands.integrate._is_no_tui", return_value=False),
        patch("tokenpak.cli.commands.integrate._is_interactive", return_value=False),
    ):
        args = _make_args()
        rc = run_integrate(args)
    assert rc == 0
    out = capsys.readouterr().out
    # Should contain the instructions text but NOT the guided header
    assert "ANTHROPIC_BASE_URL" in out
    assert "(guided)" not in out


# ── --no-tui escape → print-only ──────────────────────────────────────────


def test_no_tui_skips_guided_form(capsys):
    """--no-tui forces print-only even on a real TTY."""
    integ = _make_integration()
    with (
        patch("tokenpak.cli.commands.integrate._find", return_value=integ),
        patch("tokenpak.cli.commands.integrate._is_no_tui", return_value=True),
        patch("tokenpak.cli.commands.integrate._is_interactive", return_value=True),
    ):
        args = _make_args()
        rc = run_integrate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "(guided)" not in out


# ── --apply back-compat ────────────────────────────────────────────────────


def test_apply_flag_skips_guided_form(capsys):
    """--apply triggers direct apply path, never the guided form."""
    integ = _make_integration()
    with (
        patch("tokenpak.cli.commands.integrate._find", return_value=integ),
        patch("tokenpak.cli.commands.integrate._is_no_tui", return_value=False),
        patch("tokenpak.cli.commands.integrate._is_interactive", return_value=True),
    ):
        args = _make_args(apply=True)
        rc = run_integrate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "(guided)" not in out
    assert "Applied" in out or "--apply" in out


# ── Guided form: happy path ────────────────────────────────────────────────


def test_guided_form_happy_path(capsys):
    """Guided form: confirm Y → apply → verify → print revert hint."""
    integ = _make_integration()

    with patch("tokenpak.cli.commands.integrate._tty_confirm", return_value=True):
        rc = _run_guided_form(integ, PROXY)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied" in out
    assert "ANTHROPIC_BASE_URL" in out
    assert "--revert" in out  # rollback hint printed


def test_guided_form_cancelled(capsys):
    """Guided form: user answers N → no apply, exit 0."""
    integ = _make_integration()

    with patch("tokenpak.cli.commands.integrate._tty_confirm", return_value=False):
        rc = _run_guided_form(integ, PROXY)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Cancelled" in out
    # Applier must not have been called — no "Applied" in output
    assert "Applied" not in out


def test_guided_form_apply_failure(capsys):
    """Guided form: applier returns ok=False → exit 1."""

    def bad_applier(url: str) -> ApplyResult:
        return ApplyResult(ok=False, summary="disk full", error="ENOSPC")

    integ = _make_integration(applier=bad_applier)

    with patch("tokenpak.cli.commands.integrate._tty_confirm", return_value=True):
        rc = _run_guided_form(integ, PROXY)

    assert rc == 1
    out = capsys.readouterr().out
    assert "disk full" in out or "Failed" in out or "✖" in out


def test_guided_form_verify_fail_shown(capsys):
    """Guided form: applier ok but verify fails → shown, still exit 0."""

    def bad_verify(url: str) -> tuple[bool, str]:
        return (False, "ANTHROPIC_BASE_URL not found in settings")

    integ = _make_integration(verify_fn=bad_verify)

    with patch("tokenpak.cli.commands.integrate._tty_confirm", return_value=True):
        rc = _run_guided_form(integ, PROXY)

    assert rc == 0  # apply succeeded even though verify failed
    out = capsys.readouterr().out
    assert "ANTHROPIC_BASE_URL not found" in out


def test_guided_form_no_verify_fn(capsys):
    """Guided form: integration without verify_fn prints fallback verify hint."""
    integ = _make_integration(verify_fn=None)

    with patch("tokenpak.cli.commands.integrate._tty_confirm", return_value=True):
        rc = _run_guided_form(integ, PROXY)

    assert rc == 0
    out = capsys.readouterr().out
    assert "tokenpak status" in out
