"""CCI-21 — Mode-adoption telemetry schema tests.

Verifies that ``MetricsRecord`` includes ``active_profile`` and
``consumption_mode``, that ``record_request`` populates them correctly, and
that ``detect_consumption_mode`` returns the right value for each of the six
supported modes: cli, tui, tmux, sdk, ide, cron.

One test class per mode — 6 classes total (AC-3.13 requirement).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest  # noqa: F401 — kept for downstream pytest fixtures + markers

# TSR-07 / WS-F (2026-05-08) — relocated to tests/_internal/cli/.
# Default OSS gate excludes this directory via pyproject.toml
# `norecursedirs`; the previous TSR-01-followup try/except module-
# level skip is no longer needed.
#
# Note: the schema-drift and pure-detect carve-outs from this file
# (created by TSR-03 and TSR-04 respectively) remain at the public
# locations:
#   - tests/cli/test_anon_metrics_schema.py — 7 schema invariants
#   - tests/cli/test_consumption_mode_detect.py — 10 detect cases
# Those carve-outs continue to verify the public contract on slim OSS.
# The tests in THIS file (record_stores_*_mode + TestConfigHelpers)
# all depend on tokenpak._internal at runtime — hence the relocation.
# See tests/_internal/README.md.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path):
    from tokenpak.telemetry.anon_metrics import MetricsStore

    return MetricsStore(db_path=tmp_path / "metrics.db")


def _record_request(store, mode_env: dict, *, profile: str = "", mode: str = ""):
    """Call record_request with a fake store and supplied env/overrides."""
    from tokenpak.telemetry.anon_metrics import record_request

    with (
        mock.patch("tokenpak._internal.config.get_metrics_enabled", return_value=True),
        mock.patch("tokenpak.telemetry.anon_metrics._store", store),
        mock.patch.dict(os.environ, mode_env, clear=False),
    ):
        record_request(
            input_tokens=1000,
            output_tokens=200,
            tokens_saved=300,
            latency_ms=55.0,
            model="claude-sonnet-4-6",
            active_profile=profile,
            consumption_mode=mode,
        )


# ---------------------------------------------------------------------------
# Mode: cli
# ---------------------------------------------------------------------------


class TestModeCli:
    """CLI mode — interactive terminal, no special env vars."""

    def test_detect_cli(self):
        """detect_consumption_mode returns 'cli' in a plain terminal."""
        from tokenpak.telemetry.anon_metrics import detect_consumption_mode

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CRON_INVOCATION", "TERM_PROGRAM", "TMUX")
        }
        with (
            mock.patch.dict(os.environ, clean_env, clear=True),
            mock.patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = True
            result = detect_consumption_mode()

        assert result == "cli"

    def test_record_stores_cli_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _record_request(store, {"TOKENPAK_PROFILE": "balanced"}, mode="cli")
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].consumption_mode == "cli"
        assert pending[0].active_profile == "balanced"


# ---------------------------------------------------------------------------
# Mode: tui
# ---------------------------------------------------------------------------


class TestModeTui:
    """TUI mode — detected via explicit override (no env heuristic for tui yet)."""

    def test_record_stores_tui_mode(self, tmp_path):
        """When caller explicitly passes consumption_mode='tui', it is stored."""
        store = _make_store(tmp_path)
        _record_request(store, {"TOKENPAK_PROFILE": "claude-code-tui"}, mode="tui")
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].consumption_mode == "tui"
        assert pending[0].active_profile == "claude-code-tui"

    def test_upload_dict_includes_tui_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _record_request(store, {}, mode="tui")
        rec = store.get_pending()[0]
        d = rec.to_upload_dict()
        assert d.get("consumption_mode") == "tui"


# ---------------------------------------------------------------------------
# Mode: tmux
# ---------------------------------------------------------------------------


class TestModeTmux:
    """TMUX mode — TMUX env var set."""

    def test_detect_tmux(self):
        from tokenpak.telemetry.anon_metrics import detect_consumption_mode

        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"}, clear=False):
            result = detect_consumption_mode()

        assert result == "tmux"

    def test_record_stores_tmux_mode(self, tmp_path):
        store = _make_store(tmp_path)
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux/s"}, clear=False):
            _record_request(store, {})
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].consumption_mode == "tmux"


# ---------------------------------------------------------------------------
# Mode: sdk
# ---------------------------------------------------------------------------


class TestModeSdk:
    """SDK mode — non-interactive stdin (e.g. claude -p / piped input)."""

    def test_detect_sdk_non_interactive(self):
        from tokenpak.telemetry.anon_metrics import detect_consumption_mode

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CRON_INVOCATION", "TERM_PROGRAM", "TMUX")
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = detect_consumption_mode()

        assert result == "sdk"

    def test_record_stores_sdk_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _record_request(store, {"TOKENPAK_PROFILE": "agentic"}, mode="sdk")
        pending = store.get_pending()
        assert pending[0].consumption_mode == "sdk"
        assert pending[0].active_profile == "agentic"


# ---------------------------------------------------------------------------
# Mode: ide
# ---------------------------------------------------------------------------


class TestModeIde:
    """IDE mode — TERM_PROGRAM is vscode, cursor, or Windsurf."""

    @pytest.mark.parametrize("term_program", ["vscode", "cursor", "Windsurf"])
    def test_detect_ide(self, term_program):
        from tokenpak.telemetry.anon_metrics import detect_consumption_mode

        with mock.patch.dict(os.environ, {"TERM_PROGRAM": term_program}, clear=False):
            result = detect_consumption_mode()

        assert result == "ide"

    def test_record_stores_ide_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _record_request(store, {"TERM_PROGRAM": "vscode"}, mode="ide")
        pending = store.get_pending()
        assert pending[0].consumption_mode == "ide"


# ---------------------------------------------------------------------------
# Mode: cron
# ---------------------------------------------------------------------------


class TestModeCron:
    """Cron mode — CRON_INVOCATION env var is set."""

    def test_detect_cron(self):
        from tokenpak.telemetry.anon_metrics import detect_consumption_mode

        with mock.patch.dict(os.environ, {"CRON_INVOCATION": "1"}, clear=False):
            result = detect_consumption_mode()

        assert result == "cron"

    def test_record_stores_cron_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _record_request(store, {"CRON_INVOCATION": "1"})
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].consumption_mode == "cron"


# ---------------------------------------------------------------------------
# Schema / upload tests
# ---------------------------------------------------------------------------


class TestModeFieldsSchema:
    """Verify the new fields are included in the upload payload."""

    def test_allowed_fields_includes_new_fields(self):
        from tokenpak.telemetry.anon_metrics import MetricsRecord

        assert "active_profile" in MetricsRecord._ALLOWED_FIELDS
        assert "consumption_mode" in MetricsRecord._ALLOWED_FIELDS

    def test_to_upload_dict_includes_fields_when_set(self):
        from tokenpak.telemetry.anon_metrics import MetricsRecord

        rec = MetricsRecord(active_profile="agentic", consumption_mode="tmux")
        d = rec.to_upload_dict()
        assert d["active_profile"] == "agentic"
        assert d["consumption_mode"] == "tmux"

    def test_to_upload_dict_omits_empty_fields(self):
        from tokenpak.telemetry.anon_metrics import MetricsRecord

        rec = MetricsRecord()
        d = rec.to_upload_dict()
        # Empty strings should be omitted to keep payload minimal
        assert "active_profile" not in d
        assert "consumption_mode" not in d

    def test_schema_version_bumped_to_1_1(self):
        from tokenpak.telemetry.anon_metrics import SCHEMA_VERSION, MetricsRecord

        assert SCHEMA_VERSION == "1.1"
        rec = MetricsRecord()
        assert rec.schema_version == "1.1"

    def test_sqlite_migration_adds_columns(self, tmp_path):
        """MetricsStore migrates an existing v1.0 DB to add the new columns."""
        import sqlite3

        db_path = tmp_path / "metrics.db"

        # Simulate an old v1.0 database without the new columns
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE metrics (
                local_id TEXT PRIMARY KEY,
                date_utc TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                tokens_saved INTEGER NOT NULL DEFAULT 0,
                compression_ratio REAL NOT NULL DEFAULT 0.0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                model TEXT NOT NULL DEFAULT '',
                schema_version TEXT NOT NULL DEFAULT '1.0',
                synced INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        # Opening the store should migrate it
        from tokenpak.telemetry.anon_metrics import MetricsStore

        store = MetricsStore(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(metrics)").fetchall()}
        conn.close()

        assert "active_profile" in cols
        assert "consumption_mode" in cols

    def test_no_pii_in_upload(self, tmp_path):
        """Upload dict must not contain any PII-like fields."""
        store = _make_store(tmp_path)
        _record_request(store, {}, mode="cli", profile="balanced")
        rec = store.get_pending()[0]
        d = rec.to_upload_dict()
        for banned in ("local_id", "synced", "prompt", "content", "user", "ip"):
            assert banned not in d, f"Banned field '{banned}' in upload dict"


# ---------------------------------------------------------------------------
# config helpers (CCI-21)
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_get_active_profile_from_env(self):
        from tokenpak._internal.config import get_active_profile

        with mock.patch.dict(os.environ, {"TOKENPAK_PROFILE": "agentic"}):
            assert get_active_profile() == "agentic"

    def test_get_active_profile_default(self):
        from tokenpak._internal.config import get_active_profile

        env = {k: v for k, v in os.environ.items() if k != "TOKENPAK_PROFILE"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("tokenpak._internal.config._load", return_value={}),
        ):
            assert get_active_profile() == "balanced"

    def test_get_consumption_mode_cron(self):
        from tokenpak._internal.config import get_consumption_mode

        with mock.patch.dict(os.environ, {"CRON_INVOCATION": "1"}):
            assert get_consumption_mode() == "cron"

    def test_get_consumption_mode_ide(self):
        from tokenpak._internal.config import get_consumption_mode

        with mock.patch.dict(os.environ, {"TERM_PROGRAM": "vscode"}):
            assert get_consumption_mode() == "ide"

    def test_get_consumption_mode_tmux(self):
        from tokenpak._internal.config import get_consumption_mode

        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux"}, clear=False):
            # Remove CRON_INVOCATION and TERM_PROGRAM if set
            env = dict(os.environ)
            env.pop("CRON_INVOCATION", None)
            env.pop("TERM_PROGRAM", None)
            env["TMUX"] = "/tmp/tmux"
            with mock.patch.dict(os.environ, env, clear=True):
                assert get_consumption_mode() == "tmux"
