"""Tests for tokenpak index --watch (VaultWatcher / WatcherConfig)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.vault.watcher import (
    DEFAULT_IGNORE_PATTERNS,
    VaultWatcher,
    WatcherConfig,
    WatcherStats,
)

# ---------------------------------------------------------------------------
# WatcherConfig
# ---------------------------------------------------------------------------

class TestWatcherConfig:
    def test_defaults(self, tmp_path):
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        assert cfg.debounce_ms == 500
        assert cfg.recursive is True
        assert cfg.db_path is None
        assert cfg.ignore_patterns == DEFAULT_IGNORE_PATTERNS

    def test_custom_values(self, tmp_path):
        cfg = WatcherConfig(
            watch_paths=[str(tmp_path)],
            debounce_ms=200,
            recursive=False,
            ignore_patterns=["*.log"],
            db_path="/tmp/test.db",
        )
        assert cfg.debounce_ms == 200
        assert cfg.recursive is False
        assert cfg.ignore_patterns == ["*.log"]
        assert cfg.db_path == "/tmp/test.db"


# ---------------------------------------------------------------------------
# WatcherStats
# ---------------------------------------------------------------------------

class TestWatcherStats:
    def test_initial_values(self):
        s = WatcherStats()
        assert s.events_received == 0
        assert s.reindexes_triggered == 0
        assert s.files_reindexed == 0

    def test_uptime(self):
        s = WatcherStats()
        time.sleep(0.05)
        assert s.uptime_seconds() >= 0.04


# ---------------------------------------------------------------------------
# _should_ignore
# ---------------------------------------------------------------------------

class TestShouldIgnore:
    def _watcher(self):
        cfg = WatcherConfig(watch_paths=["/tmp"])
        return VaultWatcher(cfg)

    def test_ignores_pyc(self):
        w = self._watcher()
        assert w._should_ignore("/some/path/module.pyc")

    def test_ignores_pycache_dir(self):
        w = self._watcher()
        assert w._should_ignore("/project/__pycache__/mod.pyc")

    def test_ignores_git(self):
        w = self._watcher()
        assert w._should_ignore("/project/.git/COMMIT_EDITMSG")

    def test_does_not_ignore_py(self):
        w = self._watcher()
        assert not w._should_ignore("/project/main.py")

    def test_does_not_ignore_md(self):
        w = self._watcher()
        assert not w._should_ignore("/project/README.md")

    def test_custom_ignore_patterns(self):
        cfg = WatcherConfig(watch_paths=["/tmp"], ignore_patterns=["*.log", "secrets"])
        w = VaultWatcher(cfg)
        assert w._should_ignore("/var/app.log")
        assert w._should_ignore("/project/secrets/token.txt")
        assert not w._should_ignore("/project/main.py")


# ---------------------------------------------------------------------------
# _on_fs_event / debounce
# ---------------------------------------------------------------------------

class TestOnFsEvent:
    def test_event_increments_counter(self):
        cfg = WatcherConfig(watch_paths=["/tmp"])
        w = VaultWatcher(cfg)
        w._on_fs_event("/tmp/test.py")
        assert w._stats.events_received == 1
        w._on_fs_event("/tmp/other.py")
        assert w._stats.events_received == 2

    def test_pending_populated(self):
        cfg = WatcherConfig(watch_paths=["/tmp"])
        w = VaultWatcher(cfg)
        w._on_fs_event("/tmp/test.py")
        with w._debounce_lock:
            assert "/tmp/test.py" in w._pending

    def test_ignored_file_not_counted(self):
        cfg = WatcherConfig(watch_paths=["/tmp"])
        w = VaultWatcher(cfg)
        # .pyc should be ignored by _should_ignore but _on_fs_event
        # doesn't filter — caller (Handler) filters first
        # so direct call still registers:
        w._on_fs_event("/tmp/file.pyc")
        assert w._stats.events_received == 1


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_not_running(self, tmp_path):
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        w = VaultWatcher(cfg)
        s = w.status()
        assert s["running"] is False
        assert str(tmp_path.resolve()) in s["watched_paths"]
        assert s["debounce_ms"] == 500
        assert s["events_received"] == 0
        assert s["reindexes_triggered"] == 0
        assert s["files_reindexed"] == 0

    def test_status_keys(self, tmp_path):
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        w = VaultWatcher(cfg)
        s = w.status()
        for key in ("running", "watched_paths", "debounce_ms", "uptime_seconds",
                    "events_received", "reindexes_triggered", "files_reindexed"):
            assert key in s


# ---------------------------------------------------------------------------
# start / stop (mocked watchdog)
# ---------------------------------------------------------------------------

class TestStartStop:
    def _make_mock_watchdog(self):
        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = True

        mock_event_class = MagicMock()

        modules = {
            "watchdog": MagicMock(),
            "watchdog.observers": MagicMock(Observer=MagicMock(return_value=mock_observer)),
            "watchdog.events": MagicMock(FileSystemEventHandler=object),
        }
        return modules, mock_observer

    def test_start_schedules_observer(self, tmp_path):
        modules, mock_observer = self._make_mock_watchdog()
        with patch.dict("sys.modules", modules):
            cfg = WatcherConfig(watch_paths=[str(tmp_path)])
            w = VaultWatcher(cfg)
            w.start(blocking=False)
            mock_observer.schedule.assert_called_once()
            mock_observer.start.assert_called_once()
            w.stop()

    def test_stop_joins_observer(self, tmp_path):
        modules, mock_observer = self._make_mock_watchdog()
        with patch.dict("sys.modules", modules):
            cfg = WatcherConfig(watch_paths=[str(tmp_path)])
            w = VaultWatcher(cfg)
            w.start(blocking=False)
            w.stop()
            mock_observer.stop.assert_called_once()
            mock_observer.join.assert_called_once()
            assert not w.is_running

    def test_start_missing_watchdog_raises(self, tmp_path):
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            cfg = WatcherConfig(watch_paths=[str(tmp_path)])
            w = VaultWatcher(cfg)
            with pytest.raises((RuntimeError, ImportError)):
                w.start()


# ---------------------------------------------------------------------------
# _reindex (integration-lite with mocked registry)
# ---------------------------------------------------------------------------

class TestReindex:
    def test_reindex_skips_nonexistent(self, tmp_path):
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        w = VaultWatcher(cfg)
        w._reindex([str(tmp_path / "ghost.py")])
        assert w._stats.files_reindexed == 0

    def test_reindex_skips_unknown_extension(self, tmp_path):
        f = tmp_path / "binary.xyz"
        f.write_text("data")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        w = VaultWatcher(cfg)
        w._reindex([str(f)])
        assert w._stats.files_reindexed == 0

    def test_reindex_triggers_on_change_callback(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("print('hello')")

        called = []
        cfg = WatcherConfig(watch_paths=[str(tmp_path)])

        mock_registry = MagicMock()
        mock_registry.has_changed.return_value = True

        mock_processor = MagicMock()
        mock_processor.process.return_value = "print('hello')"

        with patch("tokenpak.registry.BlockRegistry", return_value=mock_registry), \
             patch("tokenpak.processors.get_processor", return_value=mock_processor), \
             patch("tokenpak.tokens.count_tokens", return_value=5):

            w = VaultWatcher(cfg, on_change=called.append)
            w._reindex([str(f)])

        assert str(f) in called

    def test_reindex_skips_unchanged_files(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("x = 1")

        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        mock_registry = MagicMock()
        mock_registry.has_changed.return_value = False  # no change

        with patch("tokenpak.registry.BlockRegistry", return_value=mock_registry), \
             patch("tokenpak.processors.get_processor", return_value=MagicMock()):
            w = VaultWatcher(cfg)
            w._reindex([str(f)])

        assert w._stats.files_reindexed == 0

    def test_reindex_increments_stats(self, tmp_path):
        f = tmp_path / "module.py"
        f.write_text("def foo(): pass")

        cfg = WatcherConfig(watch_paths=[str(tmp_path)])
        mock_registry = MagicMock()
        mock_registry.has_changed.return_value = True
        mock_processor = MagicMock()
        mock_processor.process.return_value = "def foo(): pass"

        with patch("tokenpak.registry.BlockRegistry", return_value=mock_registry), \
             patch("tokenpak.processors.get_processor", return_value=mock_processor), \
             patch("tokenpak.tokens.count_tokens", return_value=3):
            w = VaultWatcher(cfg)
            w._reindex([str(f)])

        assert w._stats.reindexes_triggered == 1
        assert w._stats.files_reindexed == 1


# ---------------------------------------------------------------------------
# CLI integration — tokenpak index --watch arg parsing
# ---------------------------------------------------------------------------

class TestCLIWatchArg:
    def test_watch_flag_present_in_parser(self):
        """Ensure the CLI parser accepts --watch without error."""
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["index", "/tmp", "--watch"])
        assert args.watch is True

    def test_watch_false_by_default(self):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["index", "/tmp"])
        assert args.watch is False

    def test_debounce_default(self):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["index", "/tmp", "--watch"])
        assert args.debounce == 500

    def test_debounce_custom(self):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["index", "/tmp", "--watch", "--debounce", "250"])
        assert args.debounce == 250


# ---------------------------------------------------------------------------
# .gitignore and .tokenpakignore support
# ---------------------------------------------------------------------------

class TestIgnoreFiles:
    def test_load_gitignore_patterns(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log\nbuild/\n# comment\n\n.env\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_gitignore=True)
        w = VaultWatcher(cfg)
        assert "*.log" in w._gitignore_patterns
        assert "build/" in w._gitignore_patterns
        assert ".env" in w._gitignore_patterns
        # Comments and blanks are excluded
        assert "# comment" not in w._gitignore_patterns
        assert "" not in w._gitignore_patterns

    def test_load_tokenpakignore_patterns(self, tmp_path):
        tpi = tmp_path / ".tokenpakignore"
        tpi.write_text("secrets/\n*.key\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_tokenpakignore=True)
        w = VaultWatcher(cfg)
        assert "secrets/" in w._tokenpakignore_patterns
        assert "*.key" in w._tokenpakignore_patterns

    def test_gitignore_patterns_applied_in_should_ignore(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_gitignore=True)
        w = VaultWatcher(cfg)
        assert w._should_ignore(str(tmp_path / "app.log"))
        assert not w._should_ignore(str(tmp_path / "app.py"))

    def test_tokenpakignore_patterns_applied(self, tmp_path):
        tpi = tmp_path / ".tokenpakignore"
        tpi.write_text("*.key\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_tokenpakignore=True)
        w = VaultWatcher(cfg)
        assert w._should_ignore(str(tmp_path / "secret.key"))
        assert not w._should_ignore(str(tmp_path / "secret.py"))

    def test_disable_gitignore(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_gitignore=False)
        w = VaultWatcher(cfg)
        assert w._gitignore_patterns == []
        # Pattern not applied — .log is not ignored via gitignore
        # (default patterns don't include *.log)
        assert not w._should_ignore(str(tmp_path / "app.log"))

    def test_missing_ignore_files_ok(self, tmp_path):
        # No .gitignore or .tokenpakignore present — should not raise
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_gitignore=True, use_tokenpakignore=True)
        w = VaultWatcher(cfg)
        assert w._gitignore_patterns == []
        assert w._tokenpakignore_patterns == []

    def test_negation_lines_skipped(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log\n!important.log\n")
        cfg = WatcherConfig(watch_paths=[str(tmp_path)], use_gitignore=True)
        w = VaultWatcher(cfg)
        assert "!important.log" not in w._gitignore_patterns
        assert "*.log" in w._gitignore_patterns
