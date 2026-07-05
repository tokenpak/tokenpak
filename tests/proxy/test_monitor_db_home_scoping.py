"""Scoped-TOKENPAK_HOME isolation contract for the monitor DB resolver.

Regression guard for the MONITOR_DB primary-write leak (benchmark-isolation
finding, follow-up to the pid/watchdog scoping in test_home_isolation.py):
a proxy launched with a scoped ``TOKENPAK_HOME`` used to resolve
``_paths.monitor_db(mode="write")`` — and therefore the module-level
``proxy.config.MONITOR_DB`` the primary monitor writes to — into the
*default* home, mutating the fleet's ``~/.tokenpak/monitor.db`` and
polluting rolling-cap counters. These tests pin:

- scoped ``TOKENPAK_HOME`` → the monitor DB (read + write) resolves under
  the scoped home only; the default home is never read, written, or created;
- ``TOKENPAK_DB`` / ``TOKENPAK_MONITOR_DB`` overrides still win first;
- unset ``TOKENPAK_HOME`` → behavior unchanged, including the read-migration
  candidate order AND the canonical (``~/.tpk``) fresh-write target even when
  only the legacy directory exists;
- the spend-guard readers (rolling caps, session state) converge on the same
  file the writer resolves, under default, canonical, and scoped homes.

All tests fake ``HOME`` so default-home shapes (legacy-only, canonical,
fresh) can be constructed hermetically without touching the real home.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak import _paths


def _make_valid_db(path: Path) -> Path:
    """Create a monitor DB that passes ``_paths._is_valid_monitor_db``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, ts TEXT)")
    conn.execute("INSERT INTO requests (ts) VALUES ('seed')")
    conn.commit()
    conn.close()
    return path


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Hermetic default home: HOME points at a scratch dir; env overrides clear."""
    homedir = tmp_path / "home"
    homedir.mkdir()
    monkeypatch.setenv("HOME", str(homedir))
    for var in ("TOKENPAK_HOME", "TOKENPAK_DB", "TOKENPAK_MONITOR_DB"):
        monkeypatch.delenv(var, raising=False)
    return homedir


# ── 1. Repro-pin (original leak): scoped home wins over a valid default DB ──────
def test_scoped_home_write_resolves_scoped_not_default(fake_home, tmp_path, monkeypatch):
    _make_valid_db(fake_home / ".tokenpak" / "monitor.db")
    scoped = tmp_path / "scoped"
    monkeypatch.setenv("TOKENPAK_HOME", str(scoped))

    # Pre-fix this returned the valid default-home DB — the leak.
    assert _paths.monitor_db(mode="write") == scoped / "monitor.db"
    # Scoped read must not fall back to the default home's DB either.
    assert _paths.monitor_db(mode="read") is None


# ── 2. Scoped monitor startup+write leaves the default DB byte+mtime identical ──
def test_scoped_config_monitor_write_leaves_default_untouched(fake_home, tmp_path, monkeypatch):
    default_db = _make_valid_db(fake_home / ".tokenpak" / "monitor.db")
    before = _snapshot(default_db)
    scoped = tmp_path / "scoped"
    monkeypatch.setenv("TOKENPAK_HOME", str(scoped))

    # Import proxy.config in a subprocess so its import-time
    # MONITOR_DB = _resolve_monitor_db() runs under the scoped env exactly as
    # proxy startup does, then write a row where the primary monitor would.
    script = (
        "import json, sqlite3\n"
        "from tokenpak.proxy import config as C\n"
        "p = str(C.MONITOR_DB)\n"
        "conn = sqlite3.connect(p)\n"
        "conn.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, ts TEXT)')\n"
        "conn.execute(\"INSERT INTO requests (ts) VALUES ('scoped-write')\")\n"
        "conn.commit(); conn.close()\n"
        "print(json.dumps({'monitor_db': p}))\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0, f"scoped config import failed: {res.stderr}"
    resolved = Path(json.loads(res.stdout.strip().splitlines()[-1])["monitor_db"])

    assert resolved == scoped / "monitor.db"
    assert resolved.exists()
    assert _snapshot(default_db) == before  # byte + mtime identical
    assert not (fake_home / ".tpk").exists()  # no default-home dir created


# ── 3. TOKENPAK_DB (and compat) override wins over TOKENPAK_HOME ────────────────
def test_tokenpak_db_override_wins_over_scoped_home(fake_home, tmp_path, monkeypatch):
    custom = _make_valid_db(tmp_path / "custom" / "override.db")
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "scoped"))
    monkeypatch.setenv("TOKENPAK_DB", str(custom))

    assert _paths.monitor_db(mode="read") == custom
    assert _paths.monitor_db(mode="write") == custom


def test_compat_env_override_wins_over_scoped_home(fake_home, tmp_path, monkeypatch):
    custom = _make_valid_db(tmp_path / "custom" / "compat.db")
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "scoped"))
    monkeypatch.setenv("TOKENPAK_MONITOR_DB", str(custom))

    assert _paths.monitor_db(mode="read") == custom


# ── 4. Read-migration order preserved when TOKENPAK_HOME is unset ──────────────
def test_read_migration_preserved_when_unset(fake_home):
    legacy = _make_valid_db(fake_home / ".tokenpak" / "monitor.db")
    assert _paths.monitor_db(mode="read") == legacy
    # Existing-valid DB is also the write target (unchanged behavior).
    assert _paths.monitor_db(mode="write") == legacy

    canonical = _make_valid_db(fake_home / ".tpk" / "monitor.db")
    assert _paths.monitor_db(mode="read") == canonical  # canonical precedence


def test_home_dir_fallback_candidate_preserved_when_unset(fake_home):
    fallback = _make_valid_db(fake_home / "tokenpak" / "monitor.db")
    assert _paths.monitor_db(mode="read") == fallback


# ── 5. Pin vs the uncorrected prior hunk: unset fresh-write stays canonical ─────
def test_unset_fresh_write_targets_canonical_even_when_only_legacy_dir_exists(fake_home):
    # Legacy dir exists (active resolved home) but holds no valid DB;
    # canonical ~/.tpk does not exist. Fresh write must still target
    # canonical — resolving it via the home resolver would land in legacy.
    legacy_dir = fake_home / ".tokenpak"
    legacy_dir.mkdir()
    (legacy_dir / "monitor.db").write_bytes(b"not a database")  # invalid (<100B)
    assert _paths.home() == legacy_dir  # legacy is the resolved home

    target = _paths.monitor_db(mode="write")

    assert target == fake_home / ".tpk" / "monitor.db"
    assert target.parent.is_dir()  # parent created, file not


# ── 6. Convergence: writer and spend-guard readers resolve the SAME file ───────
def _reader_paths():
    from tokenpak.proxy.spend_guard import rolling_caps, session_state

    return rolling_caps._path(None), session_state._path()


def test_convergence_default_env(fake_home):
    written = _make_valid_db(fake_home / ".tokenpak" / "monitor.db")
    caps_path, session_path = _reader_paths()
    assert _paths.monitor_db(mode="write") == written
    assert caps_path == written
    assert session_path == written


def test_convergence_canonical_home(fake_home):
    written = _make_valid_db(fake_home / ".tpk" / "monitor.db")
    caps_path, session_path = _reader_paths()
    assert _paths.monitor_db(mode="write") == written
    assert caps_path == written
    assert session_path == written


def test_convergence_scoped_home(fake_home, tmp_path, monkeypatch):
    # A default-home DB exists; the scoped run must converge on the scoped
    # file for BOTH writer and readers — never split-brain across homes.
    _make_valid_db(fake_home / ".tokenpak" / "monitor.db")
    scoped = tmp_path / "scoped"
    monkeypatch.setenv("TOKENPAK_HOME", str(scoped))

    # Before the writer creates the DB, readers must already point into the
    # scoped home (session-state falls back to the scoped home, not default).
    _caps_pre, session_pre = _reader_paths()
    assert session_pre == scoped / "monitor.db"

    written = _make_valid_db(_paths.monitor_db(mode="write"))
    caps_path, session_path = _reader_paths()
    assert written == scoped / "monitor.db"
    assert caps_path == written
    assert session_path == written


# ── 7. Static guard: resolver keeps env-var handling at candidates level ───────
def test_scoped_candidates_are_sole_home_candidates(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "scoped"))
    candidates = _paths._monitor_db_candidates()
    assert candidates == [tmp_path / "scoped" / "monitor.db"]

    # With an explicit override the override still leads the list.
    monkeypatch.setenv("TOKENPAK_DB", str(tmp_path / "override.db"))
    candidates = _paths._monitor_db_candidates()
    assert candidates[0] == tmp_path / "override.db"
    assert candidates[1:] == [tmp_path / "scoped" / "monitor.db"]
