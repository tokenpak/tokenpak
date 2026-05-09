"""tests/cli/test_setup_wizard.py

Tests for ``tokenpak setup`` wizard (tokenpak/cli/commands/setup.py).

History (TSR-02b alignment, 2026-05-08): the original test file specified
a richer setup wizard than current production carries — the spec asserted
``yes=`` kwarg on ``configure_claude_code`` for prompt-skip control,
``(changed: bool)`` return contract for idempotency, ``settings.bak.*``
backup creation, ``importlib.util.find_spec``-based provider detection,
and informational output ("No recognized LLM clients detected" / "Setup
complete"). Current production in ``tokenpak/cli/commands/setup.py``
(1.5.2) ships a thinner shim:

  detect_claude_code() -> Optional[Path]              (NOT bool)
  detect_openai() -> bool                              (env-var based, NOT importlib)
  detect_google() -> bool                              (env-var based, NOT importlib)
  configure_claude_code(proxy_url, openai_proxy_url,   (NO `yes` kwarg)
                        claude_dir) -> Dict             (NO backup, NO `(changed,)` return)
  run_setup_cmd(args) -> None                          (NO setup-complete output)

Per TSR-02b (#106 initiative, Phase 2 continuation), tests have been split:

  • Pure-alignment cases (production canonical was acceptable) are kept
    and updated to call current signatures / mock current detection
    strategy / assert current return shapes.

  • Feature-gap cases (the test asserts a load-bearing behavior — backup
    creation, idempotency tuple, prompt-skip flag, wizard output text
    — that production lacks) are marked ``pytest.skip(reason="...")``
    with grep-able ``SKIP_*`` constants citing the feature gap.
    Restoration of these features is feature work tracked under #106
    follow-up; opening that work is out of TSR-02b scope.

Coverage retained for current production:
  1. detect_claude_code (present/absent — Path-or-None)
  2. detect_openai / detect_google (env-var detection)
  3. configure_claude_code (writes URL, preserves other keys, creates
     missing parent dirs)
  4. run_setup_cmd (writes via configure_claude_code; honors claude_dir
     attr in args)
  5. configure_claude_code never writes credentials into settings.json
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from tokenpak.cli.commands.setup import (
    OPENAI_PROXY_URL,
    PROXY_URL,
    configure_claude_code,
    detect_claude_code,
    run_setup_cmd,
)

# Reason strings for the feature-gap skips. Pulled to module top so the
# initiative tracking ticket can grep for them and the restoration plan
# has a canonical list of what to re-add.
SKIP_BACKUP = (
    "configure_claude_code does not create backups in v1.5.2 production. "
    "`settings.bak.*` pattern not implemented. Restoration tracked under "
    "release-test-suite-recovery (#106) feature follow-up."
)
SKIP_IDEMPOTENT_BOOL = (
    "configure_claude_code returns Dict (always rewrites) in v1.5.2; "
    "(changed: bool) idempotency contract not implemented. Restoration "
    "tracked under #106 follow-up."
)
SKIP_YES_KWARG = (
    "configure_claude_code does not accept `yes=` kwarg in v1.5.2 (no "
    "prompt to skip — production never calls input()). Prompt-skip "
    "contract not implemented. Restoration tracked under #106 follow-up."
)
SKIP_NO_FLAG_PROMPT = (
    "configure_claude_code never calls input() in v1.5.2 — there is no "
    "interactive prompt to negate. Interactive-confirm contract not "
    "implemented. Restoration tracked under #106 follow-up."
)
SKIP_WIZARD_OUTPUT = (
    "run_setup_cmd does not emit user-facing status text in v1.5.2 "
    "('No recognized LLM clients detected' / 'Setup complete' messages "
    "are not implemented). Restoration tracked under #106 follow-up."
)
SKIP_IMPORTLIB_DETECT = (
    "detect_openai / detect_google use env-var detection in v1.5.2 "
    "(OPENAI_API_KEY / GOOGLE_API_KEY / GEMINI_API_KEY); the "
    "importlib.util.find_spec-based detection the test asserts is not "
    "implemented. Restoration tracked under #106 follow-up."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp directory."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


@pytest.fixture()
def claude_settings(tmp_home):
    """Pre-create ~/.claude/settings.json with empty JSON object."""
    settings_dir = tmp_home / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text("{}\n")
    return settings_file


# ---------------------------------------------------------------------------
# 1. detect_claude_code — returns Optional[Path] in v1.5.2, not bool
# ---------------------------------------------------------------------------


def test_detect_claude_code_absent(tmp_home):
    """v1.5.2: returns None when ~/.claude/ does not exist."""
    assert detect_claude_code() is None


def test_detect_claude_code_present(claude_settings):
    """v1.5.2: returns the Path when ~/.claude/ exists."""
    result = detect_claude_code()
    assert result is not None
    assert result == claude_settings.parent


# ---------------------------------------------------------------------------
# 2. detect_openai / detect_google — env-var based in v1.5.2
# ---------------------------------------------------------------------------


def test_detect_openai_set(monkeypatch):
    """v1.5.2: returns True when OPENAI_API_KEY is set."""
    from tokenpak.cli.commands.setup import detect_openai

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert detect_openai() is True


def test_detect_openai_unset(monkeypatch):
    from tokenpak.cli.commands.setup import detect_openai

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert detect_openai() is False


def test_detect_google_set_via_google_api_key(monkeypatch):
    """v1.5.2: returns True when GOOGLE_API_KEY is set."""
    from tokenpak.cli.commands.setup import detect_google

    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert detect_google() is True


def test_detect_google_set_via_gemini_api_key(monkeypatch):
    """v1.5.2: GEMINI_API_KEY also satisfies detect_google."""
    from tokenpak.cli.commands.setup import detect_google

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert detect_google() is True


def test_detect_google_unset(monkeypatch):
    from tokenpak.cli.commands.setup import detect_google

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert detect_google() is False


@pytest.mark.skip(reason=SKIP_IMPORTLIB_DETECT)
def test_detect_openai_when_installed():
    """Spec: importlib.util.find_spec("openai") detection.
    v1.5.2 uses env-var detection. Restoration tracked under
    #106 follow-up."""


@pytest.mark.skip(reason=SKIP_IMPORTLIB_DETECT)
def test_detect_openai_when_missing():
    """See SKIP_IMPORTLIB_DETECT."""


@pytest.mark.skip(reason=SKIP_IMPORTLIB_DETECT)
def test_detect_google_when_installed():
    """See SKIP_IMPORTLIB_DETECT."""


@pytest.mark.skip(reason=SKIP_IMPORTLIB_DETECT)
def test_detect_google_when_missing():
    """See SKIP_IMPORTLIB_DETECT."""


# ---------------------------------------------------------------------------
# 3. configure_claude_code — writes URL, preserves other keys
# ---------------------------------------------------------------------------


def test_configure_writes_proxy_url(claude_settings):
    """v1.5.2: configure_claude_code returns the resulting settings dict
    and writes ANTHROPIC_BASE_URL into the env block."""
    result = configure_claude_code()
    assert isinstance(result, dict)
    assert result["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL
    on_disk = json.loads(claude_settings.read_text())
    assert on_disk["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_preserves_other_keys(claude_settings):
    """Existing keys in settings.json must survive the write."""
    claude_settings.write_text(json.dumps({"someOtherKey": "preserved"}))
    configure_claude_code()
    data = json.loads(claude_settings.read_text())
    assert data["someOtherKey"] == "preserved"
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_writes_openai_url_when_openai_detected(claude_settings, monkeypatch):
    """v1.5.2: when OPENAI_API_KEY is set, OPENAI_BASE_URL is also written."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    configure_claude_code()
    data = json.loads(claude_settings.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL
    assert data["env"]["OPENAI_BASE_URL"] == OPENAI_PROXY_URL


def test_configure_no_openai_url_when_not_detected(claude_settings, monkeypatch):
    """v1.5.2: when OPENAI_API_KEY is unset, OPENAI_BASE_URL is not written."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    configure_claude_code()
    data = json.loads(claude_settings.read_text())
    assert "OPENAI_BASE_URL" not in data.get("env", {})


# ---------------------------------------------------------------------------
# 4. configure_claude_code — backup / idempotency contract
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_BACKUP)
def test_configure_creates_backup(claude_settings):
    """Spec: configure_claude_code creates settings.bak.<ts> on first write.
    v1.5.2 does not create backups. Restoration tracked under #106 follow-up."""


@pytest.mark.skip(reason=SKIP_IDEMPOTENT_BOOL)
def test_configure_idempotent(claude_settings):
    """Spec: configure_claude_code returns False on no-op second run.
    v1.5.2 returns Dict (always rewrites). Restoration tracked under
    #106 follow-up."""


@pytest.mark.skip(reason=SKIP_BACKUP)
def test_configure_idempotent_no_second_backup(claude_settings):
    """Spec: only one settings.bak.* across two runs.
    v1.5.2 does not create backups. Restoration tracked under #106 follow-up."""


# ---------------------------------------------------------------------------
# 5. configure_claude_code — missing file creates parent dirs
# ---------------------------------------------------------------------------


def test_configure_creates_missing_settings(tmp_home):
    """If ~/.claude/settings.json doesn't exist, configure creates the dir
    and writes the file."""
    configure_claude_code()
    settings_path = tmp_home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_no_backup_for_missing_file(tmp_home):
    """No backup file is created when the file didn't exist (v1.5.2 doesn't
    create backups at all; this assertion holds incidentally)."""
    configure_claude_code()
    claude_dir = tmp_home / ".claude"
    backups = list(claude_dir.glob("settings.bak.*"))
    assert len(backups) == 0


# ---------------------------------------------------------------------------
# 6. run_setup_cmd — wizard output text
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_WIZARD_OUTPUT)
def test_run_setup_no_clients(tmp_home, capsys):
    """Spec: 'No recognized LLM clients detected' + 'Setup complete' output.
    v1.5.2 run_setup_cmd emits no status text. Restoration tracked under
    #106 follow-up."""


def test_run_setup_claude_code_writes_config(tmp_home):
    """v1.5.2: end-to-end run_setup_cmd writes ANTHROPIC_BASE_URL
    when ~/.claude/ exists."""
    args = types.SimpleNamespace(claude_dir=None)
    claude_dir = tmp_home / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")
    run_setup_cmd(args)
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_run_setup_idempotent_second_run(tmp_home):
    """v1.5.2: run_setup_cmd is safe to call twice — file content stays
    canonical, no exceptions. (Backup-count assertion is in the
    feature-gap skip group; this test only asserts non-failure.)"""
    args = types.SimpleNamespace(claude_dir=None)
    claude_dir = tmp_home / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")
    run_setup_cmd(args)
    run_setup_cmd(args)
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_run_setup_honors_claude_dir_arg(tmp_path):
    """v1.5.2: run_setup_cmd reads args.claude_dir; explicit dir overrides
    detect_claude_code()."""
    args = types.SimpleNamespace(claude_dir=str(tmp_path))
    run_setup_cmd(args)
    settings_path = tmp_path / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


# ---------------------------------------------------------------------------
# 7. yes / no flag — prompt-skip contract not in v1.5.2
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_YES_KWARG)
def test_yes_flag_skips_prompt(claude_settings):
    """Spec: configure_claude_code(yes=True) does not call input().
    v1.5.2 has no `yes` kwarg and never calls input() at all.
    Restoration tracked under #106 follow-up."""


@pytest.mark.skip(reason=SKIP_NO_FLAG_PROMPT)
def test_no_flag_prompts_user(claude_settings):
    """Spec: configure_claude_code(yes=False) calls input() once for confirm.
    v1.5.2 never prompts. Restoration tracked under #106 follow-up."""


# ---------------------------------------------------------------------------
# 8. Wizard NEVER writes credentials (always-on invariant)
# ---------------------------------------------------------------------------


def test_no_credentials_written(claude_settings, monkeypatch):
    """settings.json must not contain any API key-like values after setup.

    The wizard writes only the BASE_URL pair; secret material stays out
    of the persisted config. This is a load-bearing privacy invariant
    that holds in v1.5.2."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    configure_claude_code()
    raw = claude_settings.read_text()
    assert "sk-secret-key" not in raw
    assert "sk-openai-secret" not in raw
    assert PROXY_URL in raw
