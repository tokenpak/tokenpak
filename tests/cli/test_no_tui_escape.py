"""tests/cli/test_no_tui_escape.py

Covers the ``--no-tui`` escape hatch at every TTY entry point:

  · bare ``tokenpak``
  · ``tokenpak integrate <X>`` (without ``--apply``)

When ``--no-tui`` is on the command line anywhere, the guided/TUI
fallbacks short-circuit to print-only deterministic output and the
flag is stripped from sys.argv before per-subcommand parsers run.
"""

from __future__ import annotations

import argparse
import sys
import types

import pytest

from tokenpak import _cli_core


@pytest.fixture(autouse=True)
def _reset_no_tui_flag(monkeypatch):
    monkeypatch.setattr(_cli_core, "_NO_TUI_FLAG", False)
    yield


# ---------------------------------------------------------------------------
# --no-tui stripping happens in main()
# ---------------------------------------------------------------------------


def test_main_strips_no_tui_from_argv(monkeypatch):
    """`tokenpak --no-tui` → sys.argv loses --no-tui, _NO_TUI_FLAG flips True."""
    monkeypatch.setattr(sys, "argv", ["tokenpak", "--no-tui"])
    # Patch isatty + run_menu so main() runs through the bare-invocation branch
    # without actually launching a TUI.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(_cli_core, "_fetch_proxy_uptime", lambda: "down")

    with pytest.raises(SystemExit) as exc:
        _cli_core.main()

    assert exc.value.code == 0
    assert "--no-tui" not in sys.argv
    assert _cli_core._no_tui() is True


def test_main_bare_with_no_tui_skips_run_menu(monkeypatch, capsys):
    """Bare `tokenpak --no-tui` on a TTY does NOT call run_menu."""
    monkeypatch.setattr(sys, "argv", ["tokenpak", "--no-tui"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(_cli_core, "_fetch_proxy_uptime", lambda: "down")

    calls = []

    def fake_run_menu():
        calls.append("menu")

    fake_menu_mod = types.ModuleType("tokenpak.cli.commands.menu")
    fake_menu_mod.run_menu = fake_run_menu
    monkeypatch.setitem(sys.modules, "tokenpak.cli.commands.menu", fake_menu_mod)

    with pytest.raises(SystemExit):
        _cli_core.main()

    assert calls == [], "run_menu must NOT have been invoked when --no-tui is set"
    out = capsys.readouterr().out
    assert "Quick Start" in out  # quick-help output is the print-only fallback


def test_main_bare_without_no_tui_uses_run_menu(monkeypatch):
    """Sanity: without --no-tui on a fully-interactive TTY, bare invocation DOES
    call run_menu.

    "Fully interactive" means both streams are a TTY AND none of the
    non-interactive opt-outs are set (CI / TOKENPAK_NONINTERACTIVE / TERM=dumb).
    The suite itself runs under CI, so those opt-outs are cleared here to model
    a real interactive terminal.
    """
    monkeypatch.setattr(sys, "argv", ["tokenpak"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TOKENPAK_NONINTERACTIVE", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(_cli_core, "_fetch_proxy_uptime", lambda: "down")

    calls = []

    def fake_run_menu():
        calls.append("menu")

    fake_menu_mod = types.ModuleType("tokenpak.cli.commands.menu")
    fake_menu_mod.run_menu = fake_run_menu
    monkeypatch.setitem(sys.modules, "tokenpak.cli.commands.menu", fake_menu_mod)

    with pytest.raises(SystemExit):
        _cli_core.main()

    assert calls == ["menu"], "run_menu should fire on TTY bare invocation"


# ---------------------------------------------------------------------------
# --no-tui on `tokenpak integrate`
# ---------------------------------------------------------------------------


def test_integrate_accepts_no_tui_and_remains_print_only(monkeypatch, capsys):
    """`tokenpak integrate claude-code --no-tui` is accepted and stays print-only.

    integrate has no TUI today, so the flag is a no-op semantically — but it
    must not error, must be stripped from argv, and the output must still
    contain the shell-aware setup instructions.
    """
    monkeypatch.setattr(sys, "argv", ["tokenpak", "integrate", "claude-code", "--no-tui"])
    monkeypatch.setenv("TOKENPAK_SHELL", "posix")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    # main() returns normally (no sys.exit) on successful subcommand dispatch.
    _cli_core.main()

    assert "--no-tui" not in sys.argv
    assert _cli_core._no_tui() is True
    out = capsys.readouterr().out
    assert "ANTHROPIC_BASE_URL" in out
    assert "API key required" in out.lower() or "no api key required" in out.lower()
