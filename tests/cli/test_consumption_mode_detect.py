"""TSR-04 / WS-D — `detect_consumption_mode()` exercise tests.

Carved out from `tests/cli/test_metrics_mode_fields.py` so the
`detect_consumption_mode()` function (restored in the same TSR-04 PR)
can be verified independently of the closed-source `tokenpak._internal`
namespace that the parent file's TSR-01-followup guard requires.

These tests probe the environment-heuristic logic only. They do not
record metrics, do not touch the SQLite store, and do not need
`tokenpak._internal`. The matching `test_record_stores_*_mode` cases
remain in the parent file (they use the `_record_request` helper,
which mocks `tokenpak._internal.config.get_metrics_enabled`) and route
to TSR-07 / WS-F when the boundary policy lands.

Covered modes: cli, tmux, sdk, ide (vscode / cursor / Windsurf), cron.
TUI is not env-detected — it is provided explicitly by the caller, so
no detect-test exists for it (the canonical TUI assertion is in the
parent file's `TestModeTui::test_record_stores_tui_mode`).

Restoration provenance:
- `27f30ec2fd` / `3a5b63cd58` (2026-04-08, CCI-21) — function shipped
- `88d3d9deb0` (2026-04-10, `_internal/` cleanup refactor) — reverted
  along with the closed-source helpers (same regression as TSR-03's
  schema fields)
- TSR-04 (this PR, 2026-05-08) — function restored verbatim from
  CCI-21; environment heuristics match `tokenpak-status/check.sh`
  CCI-09 logic; no private API used.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from tokenpak.telemetry.anon_metrics import detect_consumption_mode


def _clean_env() -> dict:
    """Return os.environ minus the env vars the heuristic looks at."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("CRON_INVOCATION", "TERM_PROGRAM", "TMUX")
    }


class TestDetectCli:
    def test_plain_terminal_returns_cli(self):
        """Interactive tty with no special env vars → 'cli'."""
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
             mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert detect_consumption_mode() == "cli"


class TestDetectTmux:
    def test_tmux_env_var_set(self):
        """TMUX=… → 'tmux' (overrides interactive tty heuristic)."""
        with mock.patch.dict(
            os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"}, clear=False
        ):
            assert detect_consumption_mode() == "tmux"


class TestDetectSdk:
    def test_non_interactive_stdin_returns_sdk(self):
        """Non-tty stdin (claude -p, piped) → 'sdk'."""
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
             mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            assert detect_consumption_mode() == "sdk"


class TestDetectIde:
    @pytest.mark.parametrize("term_program", ["vscode", "cursor", "Windsurf"])
    def test_term_program_indicates_ide(self, term_program):
        """TERM_PROGRAM ∈ {vscode, cursor, Windsurf} → 'ide'."""
        with mock.patch.dict(
            os.environ, {"TERM_PROGRAM": term_program}, clear=False
        ):
            assert detect_consumption_mode() == "ide"


class TestDetectCron:
    def test_cron_invocation_set(self):
        """CRON_INVOCATION=1 → 'cron' (highest priority — checked first)."""
        with mock.patch.dict(
            os.environ, {"CRON_INVOCATION": "1"}, clear=False
        ):
            assert detect_consumption_mode() == "cron"


class TestDetectPriority:
    """Cross-mode priority — first match wins (cron > ide > tmux > sdk > cli)."""

    def test_cron_wins_over_tmux(self):
        with mock.patch.dict(
            os.environ,
            {"CRON_INVOCATION": "1", "TMUX": "/tmp/tmux/s"},
            clear=False,
        ):
            assert detect_consumption_mode() == "cron"

    def test_ide_wins_over_tmux(self):
        with mock.patch.dict(
            os.environ,
            {"TERM_PROGRAM": "vscode", "TMUX": "/tmp/tmux/s"},
            clear=False,
        ):
            assert detect_consumption_mode() == "ide"


class TestDetectNeverRaises:
    """Function contract: never raises, returns empty string on failure."""

    def test_returns_string_under_clean_env(self):
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
             mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            result = detect_consumption_mode()
        assert isinstance(result, str)
        assert result in ("cli", "tui", "tmux", "sdk", "ide", "cron", "")
