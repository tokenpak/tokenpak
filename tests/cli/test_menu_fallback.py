# SPDX-License-Identifier: Apache-2.0
"""Tier-3 interactive fallback for the CLI menu (spec B3).

When the arrow-key picker is unavailable (Windows console, a pipe, a dumb
terminal) ``run_menu`` falls through to :func:`menu._run_plain_fallback`.
On an interactive TTY that fallback now lets the user select an option by
number or name and dispatches through the real command path; on a
non-interactive stream it stays display-only and never blocks on input.

Covers:
- PickerUnavailable routes ``run_menu`` into the interactive fallback.
- numeric selection dispatches the mapped command.
- command-name / alias selection dispatches the mapped command.
- invalid input re-prompts (no dispatch) and reports the bad option.
- clean exit via ``q`` / ``quit`` / EOF / Ctrl-C (at the prompt and mid-command).
- non-interactive stdin/stdout stays display-only and never calls ``input()``.
- the companion sub-menu item prints a launcher hint instead of dispatching.
"""

from __future__ import annotations

import sys

import pytest

from tokenpak._formatting import picker
from tokenpak.cli.commands import menu as menumod

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _record_exec(monkeypatch):
    """Replace menu._exec with a recorder; returns the list of (cmd, args)."""
    calls: list[tuple[str, str]] = []

    def fake_exec(cmd, args="", *, clear=True):
        calls.append((cmd, args))
        return 0

    monkeypatch.setattr(menumod, "_exec", fake_exec)
    return calls


def _script_input(monkeypatch, responses):
    """Feed input() a scripted sequence; an exception class/instance is raised."""
    it = iter(responses)

    def fake_input(prompt=""):
        try:
            value = next(it)
        except StopIteration:  # ran off the end -> behave like EOF
            raise EOFError
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", fake_input)


def _set_tty(monkeypatch, interactive):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: interactive, raising=False)


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("1", "start"),
        ("2", "demo"),
        ("3", "status"),
        ("4", "cost"),
        ("5", "config"),
        ("6", "permissions show"),
        ("7", "companion"),   # sentinel -> caller shows launcher hint
        ("8", "doctor"),
        ("9", "help"),
    ],
)
def test_resolve_numeric_selection(text, expected):
    assert menumod._resolve_fallback_command(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("start", "start"),      # canonical command name
        ("demo", "demo"),
        ("status", "status"),
        ("health", "status"),    # alias
        ("cost", "cost"),
        ("spend", "cost"),       # alias
        ("config", "config"),
        ("doctor", "doctor"),
        ("diag", "doctor"),      # alias
        ("help", "help"),
        ("claude", "claude"),    # direct launcher command
        ("codex", "codex"),
        ("companion", "companion"),
        ("session", "companion"),  # companion alias -> sentinel
    ],
)
def test_resolve_name_and_alias_selection(text, expected):
    assert menumod._resolve_fallback_command(text) == expected


def test_resolve_unknown_returns_none():
    assert menumod._resolve_fallback_command("totally-not-a-thing") is None


def test_resolve_table_targets_are_real_commands():
    """Every fallback command verb must be a real CLI command (no drift)."""
    from tokenpak._cli_core import _core_command_names

    real = set(_core_command_names())
    verbs = {c.split()[0] for c in menumod._HOME_FALLBACK_CMDS.values()}
    verbs |= set(menumod._FALLBACK_DIRECT_CMDS)
    missing = sorted(v for v in verbs if v not in real)
    assert missing == [], f"fallback references non-existent commands: {missing}"


# ---------------------------------------------------------------------------
# interactive dispatch
# ---------------------------------------------------------------------------

def test_numeric_selection_dispatches_command(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["2", "q"])
    menumod._run_plain_fallback()
    assert calls == [("demo", "")]


def test_command_name_selection_dispatches_command(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["status", "q"])
    menumod._run_plain_fallback()
    assert calls == [("status", "")]


def test_numeric_selection_splits_subcommand_args(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["6", "q"])  # permissions show
    menumod._run_plain_fallback()
    assert calls == [("permissions", "show")]


def test_invalid_input_reprompts_without_dispatch(monkeypatch, capsys):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["zzz", "1", "q"])
    menumod._run_plain_fallback()
    out = capsys.readouterr().out
    assert "Unknown option" in out
    assert calls == [("start", "")]  # only the valid selection dispatched


def test_companion_item_prints_hint_not_dispatch(monkeypatch, capsys):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["7", "q"])
    menumod._run_plain_fallback()
    out = capsys.readouterr().out
    assert "claude" in out and "codex" in out
    assert calls == []  # companion is a launcher pair, not a single command


def test_claude_typed_directly_dispatches(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, ["claude", "q"])
    menumod._run_plain_fallback()
    assert calls == [("claude", "")]


# ---------------------------------------------------------------------------
# clean exits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["q", "quit", "exit"])
def test_quit_words_exit_without_dispatch(monkeypatch, word):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, [word])
    menumod._run_plain_fallback()
    assert calls == []


def test_eof_at_prompt_exits_cleanly(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, [EOFError])
    menumod._run_plain_fallback()  # must not raise
    assert calls == []


def test_ctrl_c_at_prompt_exits_cleanly(monkeypatch):
    _set_tty(monkeypatch, True)
    calls = _record_exec(monkeypatch)
    _script_input(monkeypatch, [KeyboardInterrupt])
    menumod._run_plain_fallback()  # must not raise
    assert calls == []


def test_ctrl_c_during_command_exits_cleanly(monkeypatch):
    _set_tty(monkeypatch, True)

    def boom_exec(cmd, args="", *, clear=True):
        raise KeyboardInterrupt

    monkeypatch.setattr(menumod, "_exec", boom_exec)
    _script_input(monkeypatch, ["demo"])  # dispatch raises Ctrl-C
    menumod._run_plain_fallback()  # must swallow it and return


# ---------------------------------------------------------------------------
# non-interactive: display-only, never block
# ---------------------------------------------------------------------------

def test_non_interactive_stream_is_display_only(monkeypatch, capsys):
    _set_tty(monkeypatch, False)
    calls = _record_exec(monkeypatch)

    def boom_input(prompt=""):
        raise AssertionError("input() must not be called on a non-interactive stream")

    monkeypatch.setattr("builtins.input", boom_input)
    menumod._run_plain_fallback()  # must not raise, must not prompt
    out = capsys.readouterr().out
    assert "What do you want to do?" in out
    assert "tokenpak help" in out
    assert calls == []


def test_stdout_not_tty_stays_display_only(monkeypatch):
    # stdin interactive but stdout redirected -> still display-only.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    calls = _record_exec(monkeypatch)

    def boom_input(prompt=""):
        raise AssertionError("input() must not be called when stdout is redirected")

    monkeypatch.setattr("builtins.input", boom_input)
    menumod._run_plain_fallback()
    assert calls == []


# ---------------------------------------------------------------------------
# run_menu routing
# ---------------------------------------------------------------------------

def test_picker_unavailable_routes_to_fallback(monkeypatch):
    routed = {}

    def fake_fallback():
        routed["called"] = True

    monkeypatch.setattr(menumod, "_run_plain_fallback", fake_fallback)
    monkeypatch.setattr(menumod, "_header", lambda: "")
    monkeypatch.setattr(menumod, "_status_strip", lambda: "")

    def raise_unavailable(*a, **k):
        raise picker.PickerUnavailable("no tty")

    monkeypatch.setattr(menumod, "pick", raise_unavailable)
    menumod.run_menu()
    assert routed.get("called") is True
