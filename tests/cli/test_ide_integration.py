# SPDX-License-Identifier: Apache-2.0
"""L-8 (Launch Readiness, 2026-04-24): IDE integration step in `tokenpak setup`.

The wizard learned to detect Cursor / VSCode / related IDE hosts via
environment signals and offer to write `ANTHROPIC_BASE_URL` to the user's
shell profile. These tests pin:

  1. The registry pattern honors `feedback_always_dynamic` — adding a
     handler via `register()` is enough; no call-site enumeration.
  2. Detection is env-driven and returns the right handlers per signal.
  3. Shell-profile writer is idempotent and picks the right syntax per shell.
  4. `run_setup_step` is a no-op when no IDE signals are present.
  5. `run_setup_step` does not write when the user declines.
  6. `auto_yes=True` writes without prompting (for scripted setup).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak.cli import ide

# ── 1. Registry ──────────────────────────────────────────────────────────────


def test_registry_has_cursor_and_vscode():
    names = {h.name for h in ide.registered()}
    assert "cursor" in names
    assert "vscode" in names


def test_register_is_idempotent_on_name():
    before = len(ide.registered())
    ide.register(ide.IDEHandler(name="cursor", label="Cursor", detect=lambda e: False))
    after = len(ide.registered())
    assert after == before
    # Restore real detector so subsequent tests keep working.
    ide.register(
        ide.IDEHandler(
            name="cursor",
            label="Cursor",
            detect=lambda env: any(k.startswith("CURSOR_") for k in env.keys())
            or env.get("TERM_PROGRAM") == "cursor",
        )
    )


def test_register_custom_handler_is_detected():
    ide.register(ide.IDEHandler(name="fakeide", label="Fake", detect=lambda e: "FAKE_IDE" in e))
    try:
        hits = ide.detect({"FAKE_IDE": "1"})
        assert any(h.name == "fakeide" for h in hits)
    finally:
        # Remove the test handler by re-registering with a permanent-false detector
        # is sufficient for isolation; the registry is module-global.
        ide.register(ide.IDEHandler(name="fakeide", label="Fake", detect=lambda e: False))


# ── 2. Detection ─────────────────────────────────────────────────────────────


def test_detect_cursor_via_cursor_env_prefix():
    hits = {h.name for h in ide.detect({"CURSOR_TRACE_ID": "abc"})}
    assert "cursor" in hits


def test_detect_vscode_via_vscode_pid():
    hits = {h.name for h in ide.detect({"VSCODE_PID": "12345"})}
    assert "vscode" in hits


def test_detect_vscode_via_term_program():
    hits = {h.name for h in ide.detect({"TERM_PROGRAM": "vscode"})}
    assert "vscode" in hits


def test_detect_returns_empty_on_plain_shell():
    hits = ide.detect({"SHELL": "/bin/bash", "HOME": "/home/x"})
    assert hits == []


# ── 3. Shell-profile writer ──────────────────────────────────────────────────


def test_resolve_profile_prefers_existing_zshrc_when_zsh(tmp_path: Path):
    (tmp_path / ".zshrc").write_text("")
    (tmp_path / ".bashrc").write_text("")
    assert ide.resolve_profile(home=tmp_path, shell="/usr/bin/zsh") == tmp_path / ".zshrc"


def test_resolve_profile_prefers_bashrc_when_bash(tmp_path: Path):
    (tmp_path / ".bashrc").write_text("")
    (tmp_path / ".zshrc").write_text("")
    assert ide.resolve_profile(home=tmp_path, shell="/bin/bash") == tmp_path / ".bashrc"


def test_resolve_profile_returns_none_when_no_profile_exists(tmp_path: Path):
    assert ide.resolve_profile(home=tmp_path, shell="/bin/bash") is None


def test_write_export_appends_when_missing(tmp_path: Path):
    profile = tmp_path / ".zshrc"
    profile.write_text("# existing content\n")
    modified = ide.write_export(profile, "http://127.0.0.1:8766")
    assert modified is True
    text = profile.read_text()
    assert 'export ANTHROPIC_BASE_URL="http://127.0.0.1:8766"' in text
    assert "existing content" in text  # preserved


def test_write_export_is_idempotent(tmp_path: Path):
    profile = tmp_path / ".zshrc"
    profile.write_text("")
    ide.write_export(profile, "http://127.0.0.1:8766")
    modified_second = ide.write_export(profile, "http://127.0.0.1:8766")
    assert modified_second is False
    # Only one export line should be present.
    assert profile.read_text().count("ANTHROPIC_BASE_URL=") == 1


def test_write_export_uses_fish_syntax(tmp_path: Path):
    profile = tmp_path / ".config" / "fish" / "config.fish"
    profile.parent.mkdir(parents=True)
    profile.write_text("")
    ide.write_export(profile, "http://127.0.0.1:8766")
    assert "set -gx ANTHROPIC_BASE_URL" in profile.read_text()


# ── 4. run_setup_step end-to-end ─────────────────────────────────────────────


def test_run_setup_step_noop_without_ide_signals(tmp_path: Path, capsys):
    result = ide.run_setup_step(
        port=8766,
        env={"HOME": str(tmp_path)},
        home=tmp_path,
        shell="/bin/bash",
        prompt=lambda _msg: pytest.fail("should not prompt when no IDE detected"),
    )
    assert result["detected"] == []
    assert result["wrote"] is None


def test_run_setup_step_skips_when_user_declines(tmp_path: Path, capsys):
    (tmp_path / ".zshrc").write_text("")
    result = ide.run_setup_step(
        port=8766,
        env={"CURSOR_TRACE_ID": "abc"},
        home=tmp_path,
        shell="/usr/bin/zsh",
        prompt=lambda _msg: "no",
    )
    assert "cursor" in result["detected"]
    assert result["wrote"] is False
    assert "ANTHROPIC_BASE_URL" not in (tmp_path / ".zshrc").read_text()


def test_run_setup_step_writes_when_user_accepts(tmp_path: Path, capsys):
    (tmp_path / ".zshrc").write_text("")
    result = ide.run_setup_step(
        port=8766,
        env={"CURSOR_TRACE_ID": "abc"},
        home=tmp_path,
        shell="/usr/bin/zsh",
        prompt=lambda _msg: "yes",
    )
    assert "cursor" in result["detected"]
    assert result["wrote"] is True
    assert 'ANTHROPIC_BASE_URL="http://127.0.0.1:8766"' in (tmp_path / ".zshrc").read_text()


def test_run_setup_step_auto_yes_writes_without_prompt(tmp_path: Path):
    (tmp_path / ".zshrc").write_text("")
    result = ide.run_setup_step(
        port=8766,
        env={"VSCODE_PID": "12345"},
        home=tmp_path,
        shell="/usr/bin/zsh",
        prompt=lambda _msg: pytest.fail("auto_yes=True should skip the prompt"),
        auto_yes=True,
    )
    assert "vscode" in result["detected"]
    assert result["wrote"] is True


def test_run_setup_step_prints_manual_export_when_no_profile(tmp_path: Path, capsys):
    # No .zshrc / .bashrc / config.fish in tmp_path
    result = ide.run_setup_step(
        port=8766,
        env={"CURSOR_TRACE_ID": "abc"},
        home=tmp_path,
        shell="/bin/bash",
        prompt=lambda _msg: pytest.fail("should not prompt when no profile exists"),
    )
    assert "cursor" in result["detected"]
    assert result["profile"] is None
    captured = capsys.readouterr().out
    assert 'export ANTHROPIC_BASE_URL="http://127.0.0.1:8766"' in captured
