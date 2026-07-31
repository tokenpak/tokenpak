# SPDX-License-Identifier: Apache-2.0
"""The bare-invocation chooser must describe each mode truthfully.

The prompt this replaced offered "Hard (purge everything)". `--hard` does not
purge everything: it deliberately retains the session journal, the budget
history and the saved capsules. A destructive prompt that overstates its reach
is worse than a terse one, because a user who believes it thinks data is
already lost.
"""

from __future__ import annotations

import pytest

from tokenpak.cli.commands import uninstall as U


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("", "soft"),
        ("1", "soft"),
        ("s", "soft"),
        ("soft", "soft"),
        ("2", "hard"),
        ("h", "hard"),
        ("hard", "hard"),
    ],
)
def test_chooser_maps_answers(answer, expected, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: answer)
    assert U._choose_mode() == expected


@pytest.mark.parametrize("answer", ["q", "quit", "nonsense", "3"])
def test_unrecognised_and_quit_cancel(answer, monkeypatch, capsys):
    """Anything not understood cancels rather than picking a destructive default."""
    monkeypatch.setattr("builtins.input", lambda *_a: answer)
    assert U._choose_mode() is None


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_non_answer_cancels(exc, monkeypatch, capsys):
    def _raise(*_a):
        raise exc

    monkeypatch.setattr("builtins.input", _raise)
    assert U._choose_mode() is None


def test_chooser_does_not_claim_hard_removes_everything(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    out = capsys.readouterr().out
    assert "purge everything" not in out
    # It must say plainly what survives.
    assert "KEPT" in out
    assert "journal" in out and "budget" in out and "capsules" in out
    # And which option is the safe one.
    assert "recommended" in out


def test_chooser_offers_a_cancel_path(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    assert "[q] Cancel" in capsys.readouterr().out


def test_non_interactive_error_does_not_repeat_the_untruth(monkeypatch, capsys):
    """The same claim lived in the piped/scripted path, not just the prompt.

    Fixing it in the interactive chooser and leaving it verbatim one function
    over would make the command tell two different stories about the same flag,
    and the scripted message is the one every CI log carries.
    """
    monkeypatch.setattr(U.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(U.sys.stdout, "isatty", lambda: False, raising=False)
    rc = U.run_uninstall()
    assert rc == 2
    err = capsys.readouterr().err
    assert "purge everything" not in err
    assert "journal" in err and "budget" in err and "capsules" in err


@pytest.mark.parametrize("destroyed", ["Paks", "templates", "cards", "license"])
def test_both_surfaces_name_the_user_content_hard_destroys(destroyed, monkeypatch, capsys):
    """`--hard` deletes saved Paks, templates, cards and the license.

    Describing that as "configuration, caches and databases" is wrong in the
    dangerous direction: a user who trusts it keeps their Paks in their head as
    surviving, and they do not. Both the prompt and the scripted error must say
    so, or the command tells two stories again.
    """
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    assert destroyed.lower() in capsys.readouterr().out.lower()

    monkeypatch.setattr(U.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(U.sys.stdout, "isatty", lambda: False, raising=False)
    U.run_uninstall()
    assert destroyed.lower() in capsys.readouterr().err.lower()


def test_messages_hedge_so_they_cannot_go_stale(monkeypatch, capsys):
    """Both surfaces must hedge, because neither is coupled to the purge list.

    This message was wrong three times running: once by overstating what
    `--hard` removes, once by understating it, and once by reading as an
    exhaustive list that covered 13 of 18 entries. Nothing links the prose to
    `_HARD_PURGE_NAMES`, so any addition to that tuple silently makes both
    surfaces stale again. The hedge is what makes them true independent of the
    list; this test is what stops the hedge being dropped as redundant.
    """
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    assert "including" in capsys.readouterr().out

    monkeypatch.setattr(U.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(U.sys.stdout, "isatty", lambda: False, raising=False)
    U.run_uninstall()
    assert "including" in capsys.readouterr().err


def test_user_facing_purge_entries_are_named_or_hedged(monkeypatch, capsys):
    """Drift guard: a new user-facing purge target must reach the messages.

    Names that a user would recognise as their own content are called out
    individually; the rest rely on the hedge. If someone adds a user-facing
    entry to `_HARD_PURGE_NAMES`, this fails and points at the prose.
    """
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    text = capsys.readouterr().out.lower()

    # Entries a user authored or paid for, which must be named explicitly.
    must_name = {
        "paks": "paks",
        "templates": "templates",
        "cards": "cards",
        "license.json": "license",
    }
    for entry, word in must_name.items():
        assert entry in U._HARD_PURGE_NAMES, f"{entry} left the purge list — update this test"
        assert word in text, f"{entry} is destroyed but no longer named in the prompt"

    # Everything else is covered by the hedge rather than enumerated.
    assert "including" in text


# ---------------------------------------------------------------------------
# The chosen mode must DRIVE the executed plan (mutation gate).
#
# The chooser unit tests above pin _choose_mode()'s string mapping, but
# nothing below them pinned that the string reaches _build_plan. Inverting
# the chosen == "hard" branch in run_uninstall left every test in this file
# green while "[1] Un-route (recommended)" performed the hard purge
# (independent acceptance finding, staging PR #651, 2026-07-27). These two
# tests fail on that mutation: they run the real interactive path end to end
# and assert the hard= flag the plan was actually built with.
# ---------------------------------------------------------------------------


def _run_interactive(monkeypatch, answer):
    """Drive run_uninstall through the real chooser; capture _build_plan args."""
    seen = {}

    def _capture_plan(*, hard, keep_data, home):
        seen["hard"] = hard
        return [], []

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: answer)
    monkeypatch.setattr(U, "_build_plan", _capture_plan)
    rc = U.run_uninstall(dry_run=True)
    return rc, seen


def test_choosing_unroute_builds_a_soft_plan(monkeypatch, capsys):
    rc, seen = _run_interactive(monkeypatch, "1")
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["hard"] is False
    assert "--soft" in out and "--hard" not in out


def test_choosing_remove_state_builds_a_hard_plan(monkeypatch, capsys):
    rc, seen = _run_interactive(monkeypatch, "2")
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["hard"] is True
    assert "--hard" in out


def test_reversibility_claim_names_the_codex_restore_path(monkeypatch, capsys):
    """Soft mode tears down the Codex companion, and `tokenpak setup` does not
    restore it. Any reversibility claim in the chooser must therefore name the
    Codex restore path too, or it is false for Codex users (independent
    acceptance finding, staging PR #651)."""
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    U._choose_mode()
    text = capsys.readouterr().out.lower()
    if "reversible" in text:
        assert "codex --install-only" in text, (
            "chooser claims reversibility without naming the Codex restore path"
        )
