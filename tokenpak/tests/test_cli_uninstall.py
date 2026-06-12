# SPDX-License-Identifier: Apache-2.0
"""Tests for ``tokenpak uninstall --soft / --hard`` (cli.commands.uninstall).

Covers the blocking acceptance criteria:

  AC-S1  no silent destruction — bare/non-TTY rules + --hard confirmation gate
  AC-S2  dry-run parity — same ops enumerated, nothing touched, exit 0
  AC-S3  user data preserved — journal.db / budget.db / capsules/ survive --hard
  AC-S4  idempotent / partial-state safe — twice, no proxy/backup/codex, no error
  AC-S5  honesty — receipt reports only operations that occurred; failures surfaced
  AC-S6  reversibility — --soft strips routing keys from settings.json

Tests isolate HOME (for ~/.claude/settings.json + legacy ~/.tokenpak) and
TOKENPAK_HOME (for the resolved home) so nothing touches the real user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tokenpak.cli.commands import uninstall as U


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    """Point HOME + TOKENPAK_HOME at temp dirs and disable colour."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    tpk_home = tmp_path / "tpk"
    tpk_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("TOKENPAK_HOME", str(tpk_home))
    monkeypatch.setenv("NO_COLOR", "1")
    # Path.home() reads from the environment on POSIX, but be explicit.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    return {"home": fake_home, "tpk": tpk_home}


def _write_settings(fake_home: Path, env: dict) -> Path:
    p = fake_home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"env": env}, indent=2))
    return p


def _populate_tpk(tpk_home: Path) -> dict:
    """Create a representative ~/.tpk tree incl. protected user data."""
    (tpk_home / "config.json").write_text("{}")
    (tpk_home / "license.json").write_text("{}")
    (tpk_home / "telemetry.db").write_text("x")
    (tpk_home / "cache").mkdir()
    (tpk_home / "cache" / "blob").write_text("x")
    companion = tpk_home / "companion"
    companion.mkdir()
    journal = companion / "journal.db"
    budget = companion / "budget.db"
    capsules = companion / "capsules"
    capsules.mkdir()
    (capsules / "cap1.json").write_text("{}")
    journal.write_text("JOURNAL")
    budget.write_text("BUDGET")
    # An install artifact inside companion/ that SHOULD be removed.
    (companion / "mcp_state.json").write_text("{}")
    return {
        "journal": journal,
        "budget": budget,
        "capsules": capsules,
        "config": tpk_home / "config.json",
        "license": tpk_home / "license.json",
        "telemetry": tpk_home / "telemetry.db",
        "cache": tpk_home / "cache",
        "mcp_state": companion / "mcp_state.json",
    }


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the codex teardown + pip so tests never touch the real system."""
    monkeypatch.setattr(U, "_teardown_codex", lambda: (U._OUTCOME_NOOP, "codex absent"))
    # _run_pip_uninstall is only reached with --yes --hard; default to a no-op
    # that records a benign line.
    def _fake_pip(receipt):
        receipt.lines.append(
            {"phase": "package", "op": "pip uninstall tokenpak",
             "outcome": U._OUTCOME_SKIP, "detail": "stubbed"}
        )
    monkeypatch.setattr(U, "_run_pip_uninstall", _fake_pip)


# ---------------------------------------------------------------------------
# AC-S1 — no silent destruction
# ---------------------------------------------------------------------------


def test_bare_noninteractive_refuses_exit2(isolated_home, monkeypatch, capsys):
    """Bare uninstall with no flag, non-interactive → exit 2, no guess."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = U.run_uninstall()
    assert rc == 2
    err = capsys.readouterr().err
    assert "specify --soft" in err


def test_hard_non_tty_without_yes_refuses_no_deletion(isolated_home, monkeypatch, capsys):
    """Non-TTY --hard without --yes → exit 2 and nothing deleted (AC-S1)."""
    paths = _populate_tpk(isolated_home["tpk"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = U.run_uninstall(hard=True)
    assert rc == 2
    # Nothing removed.
    assert paths["config"].exists()
    assert paths["license"].exists()
    assert paths["journal"].exists()
    err = capsys.readouterr().err
    assert "--yes" in err


def test_hard_tty_confirmation_lists_paths_and_pip(isolated_home, monkeypatch, capsys):
    """TTY --hard without --yes shows every path + pip step and needs 'y'."""
    paths = _populate_tpk(isolated_home["tpk"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return "n"  # decline

    monkeypatch.setattr("builtins.input", fake_input)
    rc = U.run_uninstall(hard=True)
    assert rc == 2  # aborted
    out = capsys.readouterr().out
    # Confirmation listed the deletions + the pip step.
    assert "config.json" in out
    assert "pip uninstall tokenpak" in out
    # Declining deleted nothing.
    assert paths["config"].exists()
    assert paths["journal"].exists()


def test_hard_tty_confirm_yes_executes(isolated_home, monkeypatch):
    """Typing 'y' at the --hard confirmation proceeds with deletion."""
    paths = _populate_tpk(isolated_home["tpk"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    answers = iter(["y", "n"])  # confirm hard, decline pip
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = U.run_uninstall(hard=True)
    assert rc == 0
    assert not paths["config"].exists()
    # User data still survives.
    assert paths["journal"].exists()
    assert paths["budget"].exists()
    assert paths["capsules"].exists()


# ---------------------------------------------------------------------------
# AC-S2 — dry-run parity (touch nothing, exit 0, enumerate real ops)
# ---------------------------------------------------------------------------


def test_dry_run_soft_touches_nothing(isolated_home, capsys):
    settings = _write_settings(isolated_home["home"], {"ANTHROPIC_BASE_URL": "http://x"})
    rc = U.run_uninstall(soft=True, dry_run=True)
    assert rc == 0
    # Settings untouched by a dry-run.
    assert json.loads(settings.read_text())["env"]["ANTHROPIC_BASE_URL"] == "http://x"
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "WOULD" in out


def test_dry_run_hard_preserves_all_fixtures(isolated_home, capsys):
    """--hard --dry-run enumerates deletions but deletes nothing (AC-S2)."""
    paths = _populate_tpk(isolated_home["tpk"])
    rc = U.run_uninstall(hard=True, dry_run=True)
    assert rc == 0
    # EVERYTHING survives a dry-run.
    for key in ("config", "license", "telemetry", "cache", "mcp_state",
                "journal", "budget", "capsules"):
        assert paths[key].exists(), f"{key} should survive dry-run"


def test_dry_run_hard_parity_with_real_plan(isolated_home):
    """The dry-run plan enumerates the exact same delete ops the real run does."""
    home = isolated_home["tpk"]
    _populate_tpk(home)
    # The plan is the single source driving both dry-run and real execution.
    ops, _retained = U._build_plan(hard=True, keep_data=False, home=home)
    delete_lines = sorted(op.describe for op in ops if op.phase == "hard")
    # Spot-check the real targets are present and protected ones are absent.
    joined = "\n".join(delete_lines)
    assert "config.json" in joined
    assert "license.json" in joined
    assert "mcp_state.json" in joined
    assert "journal.db" not in joined
    assert "budget.db" not in joined
    assert "capsules" not in joined


def test_dry_run_hard_json_lists_pip_last(isolated_home, capsys):
    _populate_tpk(isolated_home["tpk"])
    rc = U.run_uninstall(hard=True, dry_run=True, output_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["mode"] == "hard"
    ops = data["operations"]
    # The pip step is the final operation.
    assert ops[-1]["op"] == "pip uninstall tokenpak"


# ---------------------------------------------------------------------------
# AC-S3 — user data never deleted by default under --hard
# ---------------------------------------------------------------------------


def test_hard_preserves_user_data(isolated_home):
    paths = _populate_tpk(isolated_home["tpk"])
    rc = U.run_uninstall(hard=True, yes=True)
    assert rc == 0
    # Protected user data survives.
    assert paths["journal"].exists()
    assert paths["journal"].read_text() == "JOURNAL"
    assert paths["budget"].exists()
    assert paths["budget"].read_text() == "BUDGET"
    assert paths["capsules"].exists()
    assert (paths["capsules"] / "cap1.json").exists()
    # Install artifacts purged.
    assert not paths["config"].exists()
    assert not paths["license"].exists()
    assert not paths["telemetry"].exists()
    assert not paths["cache"].exists()
    assert not paths["mcp_state"].exists()


def test_hard_keep_data_retains_everything(isolated_home):
    """--keep-data widens the retain set to all of ~/.tpk."""
    paths = _populate_tpk(isolated_home["tpk"])
    rc = U.run_uninstall(hard=True, yes=True, keep_data=True)
    assert rc == 0
    # Nothing under the home is removed when --keep-data is set.
    for key in ("config", "license", "telemetry", "cache",
                "journal", "budget", "capsules"):
        assert paths[key].exists(), f"{key} should be retained under --keep-data"


# ---------------------------------------------------------------------------
# AC-S4 — idempotent / partial-state safe
# ---------------------------------------------------------------------------


def test_soft_idempotent_no_state(isolated_home):
    """Soft on a system with no backup / no proxy / no codex does not error."""
    rc1 = U.run_uninstall(soft=True)
    rc2 = U.run_uninstall(soft=True)
    assert rc1 == 0
    assert rc2 == 0


def test_hard_run_twice_is_clean(isolated_home, capsys):
    _populate_tpk(isolated_home["tpk"])
    rc1 = U.run_uninstall(hard=True, yes=True)
    assert rc1 == 0
    capsys.readouterr()
    # Second run: state already gone, must still exit 0.
    rc2 = U.run_uninstall(hard=True, yes=True)
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "already clean" in out or "—" in out


def test_stop_proxy_no_pid_is_noop(isolated_home):
    outcome, detail = U._stop_proxy()
    assert outcome == U._OUTCOME_NOOP
    assert "no running proxy" in detail


# ---------------------------------------------------------------------------
# AC-S5 — honesty (only real ops reported; failures surfaced not swallowed)
# ---------------------------------------------------------------------------


def test_failure_is_reported_not_swallowed(isolated_home, monkeypatch):
    """A delete that raises is reported as a failure and bumps the exit code."""
    _populate_tpk(isolated_home["tpk"])

    real_rmtree = U.shutil.rmtree

    def boom(path, *a, **k):
        raise PermissionError(f"denied: {path}")

    monkeypatch.setattr(U.shutil, "rmtree", boom)
    rc = U.run_uninstall(hard=True, yes=True, output_json=True)
    # cache/ removal fails → errors recorded → non-zero exit.
    assert rc == 1
    # (restore in case other tests share the module state)
    monkeypatch.setattr(U.shutil, "rmtree", real_rmtree)


def test_receipt_only_reports_real_ops(isolated_home, capsys):
    """On an empty home, --hard reports no fabricated 'removed' lines."""
    rc = U.run_uninstall(hard=True, yes=True, output_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    # No operation claims a removal of a path that never existed.
    removed = [ln for ln in data["operations"] if ln["outcome"] == U._OUTCOME_DONE
               and ln["op"].startswith("Delete")]
    assert removed == []


# ---------------------------------------------------------------------------
# AC-S6 — reversibility: --soft strips routing keys from settings.json
# ---------------------------------------------------------------------------


def test_soft_strips_routing_keys_no_backup(isolated_home):
    settings = _write_settings(
        isolated_home["home"],
        {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8766",
            "OPENAI_BASE_URL": "http://127.0.0.1:8766",
            "TOKENPAK_PROFILE": "balanced",
            "USER_KEY": "keep-me",
        },
    )
    rc = U.run_uninstall(soft=True)
    assert rc == 0
    env = json.loads(settings.read_text())["env"]
    assert "ANTHROPIC_BASE_URL" not in env
    assert "OPENAI_BASE_URL" not in env
    assert "TOKENPAK_PROFILE" not in env
    # Non-tokenpak keys are preserved (reversibility, not destruction).
    assert env["USER_KEY"] == "keep-me"


def test_soft_restores_from_backup(isolated_home):
    """When a .json.bak exists, soft restores it then strips routing keys."""
    home = isolated_home["home"]
    settings = _write_settings(home, {"ANTHROPIC_BASE_URL": "http://x", "PRE": "1"})
    # Simulate the install-time backup: a clean pre-tokenpak settings.
    bak = settings.with_suffix(".json.bak")
    bak.write_text(json.dumps({"env": {"PRE": "1"}}, indent=2))
    rc = U.run_uninstall(soft=True)
    assert rc == 0
    env = json.loads(settings.read_text())["env"]
    assert "ANTHROPIC_BASE_URL" not in env
    assert env.get("PRE") == "1"


def test_soft_round_trip_setup_restores(isolated_home):
    """After --soft, re-applying configure_settings re-routes (reversibility)."""
    from tokenpak.cli.commands import install as I

    settings = _write_settings(isolated_home["home"], {"ANTHROPIC_BASE_URL": "http://x"})
    U.run_uninstall(soft=True)
    env = json.loads(settings.read_text())["env"]
    assert "ANTHROPIC_BASE_URL" not in env
    # `tokenpak setup`-equivalent re-routes.
    I.configure_settings(mode="cli", proxy_url="http://127.0.0.1:8766")
    env2 = json.loads(settings.read_text())["env"]
    assert env2["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8766"


# ---------------------------------------------------------------------------
# Misc — flag validation
# ---------------------------------------------------------------------------


def test_soft_and_hard_together_is_error(isolated_home, capsys):
    rc = U.run_uninstall(soft=True, hard=True)
    assert rc == 2
    assert "only one of" in capsys.readouterr().err


def test_json_error_shape_on_refusal(isolated_home, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = U.run_uninstall(output_json=True)
    assert rc == 2
    err = capsys.readouterr().err
    assert json.loads(err)["error"]
