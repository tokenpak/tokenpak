"""Tests for tokenpak replay CLI commands (task: p1-tokenpak-replay-cli)."""

import json
from unittest.mock import patch

import pytest

# WS-A residual import guard — TSR-01-followup.
# tokenpak.tokens is referenced transitively from the replay CLI imports
# (via tokenpak.cli's lazy module wiring); it is not part of the slim
# OSS surface. Skip cleanly when absent.
pytest.importorskip(
    "tokenpak.tokens",
    reason="tokenpak.tokens not part of slim OSS surface (replay-CLI dep)",
)

from tokenpak.cli import build_parser, cmd_replay_list, cmd_replay_run, cmd_replay_show
from tokenpak.telemetry.replay import ReplayEntry, ReplayStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store_with_entries() -> tuple[ReplayStore, list]:
    """Create an in-memory store populated with two test entries."""
    store = ReplayStore(":memory:")

    msgs = [
        {"role": "user", "content": "Please compress this document. " * 30},
        {"role": "assistant", "content": "Done."},
    ]
    e1 = ReplayEntry.new(
        provider="anthropic", model="claude-3-haiku",
        input_tokens_raw=1000, input_tokens_sent=700, tokens_saved=300,
        cost_usd=0.001, messages=msgs,
    )
    e2 = ReplayEntry.new(
        provider="openai", model="gpt-4o",
        input_tokens_raw=2000, input_tokens_sent=1400, tokens_saved=600,
        cost_usd=0.004,
    )
    store.capture(e1)
    store.capture(e2)
    return store, [e1, e2]


def make_args(**kwargs):
    """Create a simple namespace object for CLI args."""
    import argparse
    ns = argparse.Namespace(**kwargs)
    return ns


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestReplayParser:
    def test_replay_list_parses(self):
        p = build_parser()
        args = p.parse_args(["replay", "list"])
        assert args.replay_cmd == "list"
        assert args.limit == 20
        assert args.provider is None

    def test_replay_list_with_options(self):
        p = build_parser()
        args = p.parse_args(["replay", "list", "--limit", "5", "--provider", "anthropic"])
        assert args.limit == 5
        assert args.provider == "anthropic"

    def test_replay_show_parses(self):
        p = build_parser()
        args = p.parse_args(["replay", "show", "abc123"])
        assert args.replay_cmd == "show"
        assert args.id == "abc123"
        assert args.show_messages is False

    def test_replay_show_with_messages_flag(self):
        p = build_parser()
        args = p.parse_args(["replay", "show", "abc123", "--messages"])
        assert args.show_messages is True

    def test_replay_run_parses(self):
        p = build_parser()
        args = p.parse_args(["replay", "run", "abc123"])
        assert args.replay_cmd == "run"
        assert args.id == "abc123"
        assert args.no_compress is False
        assert args.aggressive is False
        assert args.diff is False

    def test_replay_run_flags(self):
        p = build_parser()
        args = p.parse_args(["replay", "run", "abc123", "--model", "gpt-4", "--aggressive", "--diff"])
        assert args.model == "gpt-4"
        assert args.aggressive is True
        assert args.diff is True

    def test_replay_run_no_compress(self):
        p = build_parser()
        args = p.parse_args(["replay", "run", "abc123", "--no-compress"])
        assert args.no_compress is True


# ---------------------------------------------------------------------------
# cmd_replay_list tests
# ---------------------------------------------------------------------------

class TestCmdReplayList:
    def test_list_empty(self, capsys):
        args = make_args(limit=20, provider=None)
        with patch("tokenpak.cli._replay_store_path", return_value=":memory:"), \
             patch("tokenpak._cli_core._get_replay_store") as mock_store:
            from tokenpak.telemetry.replay import ReplayStore
            mock_store.return_value = ReplayStore(":memory:")
            cmd_replay_list(args)
        out = capsys.readouterr().out
        assert "No replay entries" in out

    def test_list_shows_entries(self, capsys):
        store, entries = make_store_with_entries()
        args = make_args(limit=20, provider=None)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_list(args)
        out = capsys.readouterr().out
        assert entries[0].replay_id in out
        assert entries[1].replay_id in out
        assert "anthropic/claude-3-haiku" in out
        assert "openai/gpt-4o" in out

    def test_list_shows_content_indicator(self, capsys):
        store, entries = make_store_with_entries()
        args = make_args(limit=20, provider=None)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_list(args)
        out = capsys.readouterr().out
        assert "📦" in out  # e1 has messages

    def test_list_provider_filter(self, capsys):
        store, entries = make_store_with_entries()
        args = make_args(limit=20, provider="anthropic")
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_list(args)
        out = capsys.readouterr().out
        assert "anthropic" in out
        assert "openai" not in out

    def test_list_limit(self, capsys):
        store, entries = make_store_with_entries()
        args = make_args(limit=1, provider=None)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_list(args)
        out = capsys.readouterr().out
        # Only 1 entry shown
        assert "1 entry" in out


# ---------------------------------------------------------------------------
# cmd_replay_show tests
# ---------------------------------------------------------------------------

class TestCmdReplayShow:
    def test_show_valid_id(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, show_messages=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_show(args)
        out = capsys.readouterr().out
        assert e.replay_id in out
        assert "anthropic" in out
        assert "claude-3-haiku" in out
        assert "2 message(s) captured" in out

    def test_show_missing_id_exits(self, capsys):
        store, _ = make_store_with_entries()
        args = make_args(id="doesnotexist", show_messages=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            with pytest.raises(SystemExit):
                cmd_replay_show(args)

    def test_show_no_content(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[1]  # no messages
        args = make_args(id=e.replay_id, show_messages=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_show(args)
        out = capsys.readouterr().out
        assert "not captured" in out

    def test_show_messages_flag(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, show_messages=True)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_show(args)
        out = capsys.readouterr().out
        assert "compress this document" in out


# ---------------------------------------------------------------------------
# cmd_replay_run tests
# ---------------------------------------------------------------------------

class TestCmdReplayRun:
    def test_run_standard_compression(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model=None, no_compress=False, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        assert "standard compression" in out
        assert "Raw tokens" in out
        assert "Result tokens" in out

    def test_run_aggressive_compression(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model=None, no_compress=False, aggressive=True, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        assert "aggressive compression" in out

    def test_run_no_compress(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model=None, no_compress=True, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        assert "no compression" in out
        assert "Saved" in out

    def test_run_with_model_label(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model="claude-3-opus", no_compress=False, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        assert "claude-3-opus" in out

    def test_run_diff_flag(self, capsys):
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model=None, no_compress=False, aggressive=True, diff=True)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        assert "Diff" in out

    def test_run_missing_id_exits(self):
        store, _ = make_store_with_entries()
        args = make_args(id="ghost", model=None, no_compress=False, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            with pytest.raises(SystemExit):
                cmd_replay_run(args)

    def test_run_no_content_exits(self):
        store, entries = make_store_with_entries()
        e = entries[1]  # no messages
        args = make_args(id=e.replay_id, model=None, no_compress=False, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            with pytest.raises(SystemExit):
                cmd_replay_run(args)

    def test_run_no_compress_tokens_equal_raw(self, capsys):
        """With --no-compress, result tokens should equal raw token count."""
        from tokenpak.tokens import count_tokens
        store, entries = make_store_with_entries()
        e = entries[0]
        args = make_args(id=e.replay_id, model=None, no_compress=True, aggressive=False, diff=False)
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_run(args)
        out = capsys.readouterr().out
        # Result tokens should match raw
        raw = count_tokens(json.dumps(e.messages))
        assert f"Raw tokens    : {raw:,}" in out
        assert f"Result tokens : {raw:,}" in out


class TestReplayClearCLI:
    def test_clear_empty_store(self, capsys):
        from unittest.mock import MagicMock, patch

        from tokenpak.cli import cmd_replay_clear
        store = ReplayStore(":memory:")
        args = MagicMock()
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_clear(args)
        out = capsys.readouterr().out
        assert "0" in out
        assert "entries" in out or "entry" in out

    def test_clear_with_entries(self, capsys):
        from unittest.mock import MagicMock, patch

        from tokenpak.cli import cmd_replay_clear
        store, _ = make_store_with_entries()
        assert store.count() > 0
        args = MagicMock()
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            cmd_replay_clear(args)
        out = capsys.readouterr().out
        assert store.count() == 0
        assert "Cleared" in out

    def test_clear_via_argparse(self, capsys):
        """End-to-end: tokenpak replay clear via CLI parser."""
        import argparse
        from unittest.mock import patch

        from tokenpak.cli import _build_replay_parser
        store, _ = make_store_with_entries()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        _build_replay_parser(sub)
        args = parser.parse_args(["replay", "clear"])
        with patch("tokenpak._cli_core._get_replay_store", return_value=store):
            args.func(args)
        out = capsys.readouterr().out
        assert "Cleared" in out
        assert store.count() == 0
