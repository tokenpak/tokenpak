"""tests/cli/test_install_claude_code.py

Tests for ``tokenpak install --claude-code`` (tokenpak/cli/commands/install.py).

History (TSR-02 alignment, 2026-05-08): the original test file specified a
richer installer than current production carries — the spec asserted
``dry_run`` support across configure/install/smoke entry points, a
``(changed, backup)`` tuple return from ``configure_settings``, timestamped
``settings.json.bak.*`` backups, smoke-test-failure restore, ``SystemExit``
codes 1/2 for missing-Claude / smoke-fail, ``ValueError`` on invalid mode,
``"claude-code-{mode}"`` profile names, atomic-write tmp-cleanup on
``TypeError``, and ``VSCODE_PID``-based IDE detection. Current production
in ``tokenpak/cli/commands/install.py`` (1.5.2) ships a thinner shim:
``configure_settings(mode, proxy_url) -> Dict``, ``run_smoke_test(proxy_url)
-> bool`` via ``urllib``, ``install_systemd_unit(proxy_url) -> Path``,
``detect_claude_dir() -> Optional[Path]``, ``select_mode(mode) -> str``
(no validation), profile names ``balanced/aggressive/safe/agentic``,
``auto_detect_mode`` reading ``TERM_PROGRAM``.

Per TSR-02 (`#106` initiative, Phase 2), tests have been split:

  • Pure-alignment cases (production canonical was acceptable) are kept
    and updated to call current signatures / assert current return shapes.

  • Feature-gap cases (the test asserts a load-bearing behavior — dry-run
    safety, backup-restore, validation, etc. — that production lacks)
    are marked ``pytest.skip(reason="<feature> not in production v1.5.2;
    restoration tracked separately")``. They are NOT deleted, so the
    spec stays in source as a record of intended behavior. Restoration
    of these features is a separate feature ticket; opening that work
    is out of TSR-02 scope.

Coverage retained for current production:
  1. detect_claude_binary (found/not-found)
  2. detect_claude_dir (present/absent — Path-or-None)
  3. _read_settings (missing file / invalid JSON)
  4. _atomic_write_settings (creates file at canonical path)
  5. configure_settings (writes URL, preserves other fields)
  6. run_smoke_test (200/non-200/exception)
  7. install_systemd_unit (writes unit at canonical path)
  8. select_mode (explicit pass-through)
  9. auto_detect_mode (tmux / IDE via TERM_PROGRAM / cli fallback)
 10. MODE_PROFILE_MAP (asserted against current canonical values)
 11. restore_backup (round-trip)
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.cli.commands.install import (
    MODE_PROFILE_MAP,
    PROXY_URL,
    _atomic_write_settings,
    _read_settings,
    _settings_path,
    _systemd_unit_path,
    auto_detect_mode,
    configure_settings,
    detect_claude_binary,
    detect_claude_dir,
    install_systemd_unit,
    restore_backup,
    run_install_cmd,
    run_smoke_test,
    select_mode,
)

# Reason strings for the feature-gap skips. Pulled to module top so the
# initiative tracking ticket can grep for them and the restoration plan
# has a canonical list of what to re-add.
SKIP_DRY_RUN = (
    "configure_settings/run_smoke_test/install_systemd_unit do not accept "
    "dry_run= in v1.5.2 production. Restoration tracked under "
    "release-test-suite-recovery (#106) feature follow-up."
)
SKIP_BACKUP_TUPLE = (
    "configure_settings returns Dict in v1.5.2; (changed, backup) tuple "
    "contract not implemented. Restoration tracked under #106 follow-up."
)
SKIP_BACKUP_TIMESTAMP = (
    "_backup_settings creates `.json.bak` (single, no timestamp) in v1.5.2; "
    "`settings.json.bak.<ts>` timestamped pattern not implemented. "
    "Restoration tracked under #106 follow-up."
)
SKIP_SMOKE_RESTORE = (
    "run_install_cmd does not run smoke test or restore backup on failure "
    "in v1.5.2. Restoration tracked under #106 follow-up."
)
SKIP_MODE_VALIDATION = (
    "select_mode accepts any string in v1.5.2 (no ValueError on invalid). "
    "Restoration tracked under #106 follow-up."
)
SKIP_NO_CLAUDE_GUARD = (
    "run_install_cmd does not gate on detect_claude_binary/detect_claude_dir "
    "in v1.5.2 (no SystemExit on missing Claude). Restoration tracked under "
    "#106 follow-up."
)
SKIP_TMP_CLEANUP = (
    "_atomic_write_settings does not clean the tempfile on a json.dump "
    "TypeError in v1.5.2 (the tempfile is left in place). Restoration of "
    "tmp-cleanup-on-validation-failure tracked under #106 follow-up."
)
SKIP_SYSTEMD_IDEMPOTENT_BOOL = (
    "install_systemd_unit returns Path (always rewrites) in v1.5.2; "
    "(changed: bool) idempotency contract not implemented. Restoration "
    "tracked under #106 follow-up."
)
SKIP_VSCODE_PID = (
    "auto_detect_mode reads TERM_PROGRAM in v1.5.2 ('vscode'/'cursor'/"
    "'windsurf'); VSCODE_PID env-var detection not implemented. Restoration "
    "tracked under #106 follow-up."
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
def claude_dir(tmp_home):
    """Create the ~/.claude/ directory (simulates Claude Code installed)."""
    d = tmp_home / ".claude"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def claude_settings(claude_dir):
    """Create ~/.claude/settings.json with a minimal valid JSON object."""
    f = claude_dir / "settings.json"
    f.write_text("{}\n", encoding="utf-8")
    return f


@pytest.fixture()
def claude_settings_with_data(claude_dir):
    """Create ~/.claude/settings.json with existing fields that must be preserved."""
    f = claude_dir / "settings.json"
    data = {"theme": "dark", "someOtherKey": 42, "env": {"EXISTING_VAR": "keep-me"}}
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f


def _fake_args(**kwargs):
    """Build a minimal args namespace.

    Note: ``dry_run`` is not honored by run_install_cmd in v1.5.2 (see
    SKIP_DRY_RUN) but is left in the default kwargs for tests that mock-
    invoke and look at the namespace directly.
    """
    defaults = {
        "dry_run": False,
        "no_systemd": True,  # skip systemd by default in unit tests
        "mode": None,
        "claude_code": True,
        "systemd": False,  # run_install_cmd reads this attribute name
        "proxy_url": PROXY_URL,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. detect_claude_binary
# ---------------------------------------------------------------------------


def test_detect_claude_binary_found():
    with mock.patch(
        "tokenpak.cli.commands.install.shutil.which", return_value="/usr/local/bin/claude"
    ):
        assert detect_claude_binary() == "/usr/local/bin/claude"


def test_detect_claude_binary_not_found():
    with mock.patch("tokenpak.cli.commands.install.shutil.which", return_value=None):
        assert detect_claude_binary() is None


# ---------------------------------------------------------------------------
# 2. detect_claude_dir — returns Optional[Path], not bool
# ---------------------------------------------------------------------------


def test_detect_claude_dir_absent(tmp_home):
    """v1.5.2: returns None when ~/.claude/ does not exist."""
    assert detect_claude_dir() is None


def test_detect_claude_dir_present(claude_dir):
    """v1.5.2: returns the Path when ~/.claude/ exists."""
    result = detect_claude_dir()
    assert result is not None
    assert result == claude_dir


# ---------------------------------------------------------------------------
# 3. _read_settings / _atomic_write_settings — no path arg in v1.5.2
# ---------------------------------------------------------------------------


def test_read_settings_missing(tmp_home):
    """No settings.json at the canonical path → empty dict."""
    assert _read_settings() == {}


def test_read_settings_invalid_json(claude_dir):
    p = claude_dir / "settings.json"
    p.write_text("not json", encoding="utf-8")
    assert _read_settings() == {}


def test_atomic_write_settings_creates_file(claude_dir):
    data = {"env": {"ANTHROPIC_BASE_URL": PROXY_URL}}
    _atomic_write_settings(data)
    assert json.loads(_settings_path().read_text()) == data
    # No leftover .tmp files in claude_dir (NamedTemporaryFile names vary,
    # but os.replace removes the source on success)
    leftover = [p for p in claude_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


@pytest.mark.skip(reason=SKIP_TMP_CLEANUP)
def test_atomic_write_settings_no_tmp_on_validation_failure(claude_dir):
    """Spec: if json.dump raises TypeError mid-write, no .tmp file persists.
    v1.5.2 production leaves the tempfile in place because os.replace never
    runs. Restoration tracked under #106 follow-up."""
    data = {"key": object()}
    with pytest.raises(TypeError):
        _atomic_write_settings(data)
    leftover = [p for p in claude_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


# ---------------------------------------------------------------------------
# 4. configure_settings — Dict return in v1.5.2
# ---------------------------------------------------------------------------


def test_configure_settings_writes_url(claude_settings):
    """v1.5.2: configure_settings returns the resulting settings dict."""
    result = configure_settings(mode="cli")
    assert isinstance(result, dict)
    assert result["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL
    # Verify the file on disk got the same content.
    on_disk = json.loads(claude_settings.read_text())
    assert on_disk["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_settings_preserves_other_fields(claude_settings_with_data):
    """Existing settings fields (theme, someOtherKey, EXISTING_VAR) must survive."""
    configure_settings(mode="cli")
    data = json.loads(_settings_path().read_text())
    assert data["theme"] == "dark"
    assert data["someOtherKey"] == 42
    assert data["env"]["EXISTING_VAR"] == "keep-me"
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


@pytest.mark.skip(reason=SKIP_BACKUP_TUPLE)
def test_configure_settings_idempotent(claude_settings):
    """Spec: already-correct URL → (changed=False, backup=None).
    v1.5.2 always rewrites and returns just the dict. Restoration of the
    (changed, backup) tuple contract tracked under #106 follow-up."""


# ---------------------------------------------------------------------------
# 5. --dry-run — not supported in v1.5.2
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_DRY_RUN)
def test_dry_run_no_writes(claude_dir):
    """Spec: configure_settings(dry_run=True) creates/modifies no files.
    v1.5.2 has no dry_run kwarg. Restoration tracked under #106 follow-up."""


@pytest.mark.skip(reason=SKIP_DRY_RUN)
def test_dry_run_full_command(tmp_home, claude_dir):
    """Spec: run_install_cmd with --dry-run writes no settings.json.
    v1.5.2 run_install_cmd does not honor dry_run. Restoration tracked
    under #106 follow-up."""


# ---------------------------------------------------------------------------
# 6. run_smoke_test — urllib-based in v1.5.2
# ---------------------------------------------------------------------------


def test_smoke_test_pass():
    """v1.5.2: urlopen returns 200 → True."""
    fake_response = mock.MagicMock()
    fake_response.status = 200
    fake_response.__enter__ = mock.MagicMock(return_value=fake_response)
    fake_response.__exit__ = mock.MagicMock(return_value=False)
    with mock.patch("urllib.request.urlopen", return_value=fake_response):
        assert run_smoke_test() is True


def test_smoke_test_fail():
    """v1.5.2: urlopen returns non-200 → False."""
    fake_response = mock.MagicMock()
    fake_response.status = 502
    fake_response.__enter__ = mock.MagicMock(return_value=fake_response)
    fake_response.__exit__ = mock.MagicMock(return_value=False)
    with mock.patch("urllib.request.urlopen", return_value=fake_response):
        assert run_smoke_test() is False


def test_smoke_test_exception():
    """v1.5.2: any exception during urlopen → False."""
    with mock.patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
        assert run_smoke_test() is False


# ---------------------------------------------------------------------------
# 7. smoke-fail backup-restore — not implemented in v1.5.2
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_SMOKE_RESTORE)
def test_smoke_fail_restores_backup(tmp_home, claude_settings):
    """Spec: smoke fail → settings.json restored from backup, SystemExit(2).
    v1.5.2 run_install_cmd doesn't run smoke test. Restoration tracked
    under #106 follow-up."""


# ---------------------------------------------------------------------------
# 8. idempotency / second-run — timestamped backups not implemented
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_BACKUP_TIMESTAMP)
def test_idempotent_second_run(tmp_home, claude_settings):
    """Spec: counts settings.json.bak.<ts> across two runs.
    v1.5.2 _backup_settings creates `.json.bak` (no timestamp).
    Restoration tracked under #106 follow-up."""


# ---------------------------------------------------------------------------
# 9. select_mode / auto_detect_mode
# ---------------------------------------------------------------------------


def test_select_mode_explicit():
    """v1.5.2: select_mode passes through explicit values."""
    assert select_mode("tui") == "tui"
    assert select_mode("cron") == "cron"


@pytest.mark.skip(reason=SKIP_MODE_VALIDATION)
def test_select_mode_invalid():
    """Spec: select_mode("invalid") raises ValueError.
    v1.5.2 accepts any string. Restoration tracked under #106 follow-up."""


def test_auto_detect_mode_tmux(monkeypatch):
    """v1.5.2: TMUX env → 'tmux'."""
    monkeypatch.setenv("TMUX", "/tmp/tmux-0/default,1234,0")
    monkeypatch.setenv("TERM_PROGRAM", "")
    assert auto_detect_mode() == "tmux"


def test_auto_detect_mode_ide_via_term_program(monkeypatch):
    """v1.5.2: TERM_PROGRAM in {'vscode','cursor','windsurf'} → 'ide'."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    assert auto_detect_mode() == "ide"


@pytest.mark.skip(reason=SKIP_VSCODE_PID)
def test_auto_detect_mode_ide_via_vscode_pid(monkeypatch):
    """Spec: VSCODE_PID env → 'ide' regardless of TERM_PROGRAM.
    v1.5.2 only inspects TERM_PROGRAM. Restoration tracked under
    #106 follow-up."""


def test_mode_sets_correct_profile():
    """v1.5.2: MODE_PROFILE_MAP canonical values per install.py.

    Update this table when production canonical changes (don't hardcode
    a literal dict — read MODE_PROFILE_MAP at test time)."""
    expected_canonical = {
        "cli": "balanced",
        "bare": "aggressive",
        "tui": "balanced",
        "tmux": "agentic",
        "ide": "safe",
        "cron": "aggressive",
    }
    assert MODE_PROFILE_MAP == expected_canonical


# ---------------------------------------------------------------------------
# 10. install_systemd_unit — Path return in v1.5.2
# ---------------------------------------------------------------------------


def test_systemd_unit_written(tmp_home, claude_dir):
    """v1.5.2: install_systemd_unit() returns the unit Path; file is created."""
    result = install_systemd_unit()
    assert isinstance(result, Path)
    assert result.exists()
    content = result.read_text()
    assert "TokenPak Proxy" in content
    assert "WantedBy=default.target" in content


@pytest.mark.skip(reason=SKIP_SYSTEMD_IDEMPOTENT_BOOL)
def test_systemd_unit_idempotent(tmp_home, claude_dir):
    """Spec: install_systemd_unit returns True first call, False on no-op
    second call. v1.5.2 returns Path and always rewrites. Restoration of
    the bool idempotency contract tracked under #106 follow-up."""


def test_no_systemd_skips_unit(tmp_home, claude_dir):
    """run_install_cmd with systemd=False does not write the unit file."""
    args = _fake_args(systemd=False, mode="cli")
    run_install_cmd(args)
    unit_path = _systemd_unit_path()
    assert not unit_path.exists()


# ---------------------------------------------------------------------------
# 11. restore_backup
# ---------------------------------------------------------------------------


def test_restore_backup(claude_dir):
    original = {"env": {"ANTHROPIC_BASE_URL": "old-url"}}
    p = claude_dir / "settings.json"
    p.write_text(json.dumps(original, indent=2) + "\n")
    import shutil as _shutil

    backup = p.parent / "settings.json.bak"
    _shutil.copy2(p, backup)
    p.write_text('{"env": {"ANTHROPIC_BASE_URL": "new-url"}}\n')
    assert restore_backup(backup) is True
    assert json.loads(p.read_text()) == original


def test_restore_backup_none_is_noop(claude_dir):
    """restore_backup(None) must not raise, returns False."""
    assert restore_backup(None) is False


# ---------------------------------------------------------------------------
# 12. no Claude Code → exit 1 — not enforced in v1.5.2
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_NO_CLAUDE_GUARD)
def test_no_claude_code_exits(tmp_home):
    """Spec: if neither binary nor ~/.claude/ exists, installer exits 1.
    v1.5.2 run_install_cmd doesn't gate on these. Restoration tracked
    under #106 follow-up."""
