"""Tests for the tokenpak diff command."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from tokenpak.agent.cli.commands.diff import (
    DiffBlock,
    ContextDiff,
    _classify_segment,
    _is_pinned,
    _build_diff_from_segments,
    _empty_diff,
    print_diff,
    run_diff_cmd,
)


# ---------------------------------------------------------------------------
# DiffBlock
# ---------------------------------------------------------------------------

def test_diffblock_symbol_removed():
    b = DiffBlock("id1", "My Block", "removed")
    assert b.symbol == "+"


def test_diffblock_symbol_compressed():
    b = DiffBlock("id2", "Compressed Block", "compressed")
    assert b.symbol == "~"


def test_diffblock_symbol_retained():
    b = DiffBlock("id3", "Pinned Block", "retained", pinned=True)
    assert b.symbol == "="


def test_diffblock_to_dict_includes_symbol():
    b = DiffBlock("id4", "Test", "removed", tokens_before=100, tokens_after=0)
    d = b.to_dict()
    assert d["symbol"] == "+"
    assert d["status"] == "removed"
    assert d["tokens_before"] == 100


# ---------------------------------------------------------------------------
# _classify_segment
# ---------------------------------------------------------------------------

def test_classify_removed_by_action():
    seg = {"actions": '["remove"]', "tokens_raw": 100, "tokens_after_tp": 0}
    assert _classify_segment(seg) == "removed"


def test_classify_compressed_by_action():
    seg = {"actions": '["compress"]', "tokens_raw": 200, "tokens_after_tp": 80}
    assert _classify_segment(seg) == "compressed"


def test_classify_retained_by_default():
    seg = {"actions": "[]", "tokens_raw": 50, "tokens_after_tp": 50}
    assert _classify_segment(seg) == "retained"


def test_classify_removed_by_token_count():
    """Zero tokens_after with non-zero raw should imply removed."""
    seg = {"actions": "[]", "tokens_raw": 100, "tokens_after_tp": 0}
    assert _classify_segment(seg) == "removed"


def test_classify_compressed_by_token_ratio():
    """Significant token reduction without explicit action → compressed."""
    seg = {"actions": "[]", "tokens_raw": 1000, "tokens_after_tp": 100}
    assert _classify_segment(seg) == "compressed"


# ---------------------------------------------------------------------------
# _is_pinned
# ---------------------------------------------------------------------------

def test_is_pinned_true():
    seg = {"content_type": "pinned_instruction", "segment_type": "instruction", "segment_source": ""}
    assert _is_pinned(seg) is True


def test_is_pinned_false():
    seg = {"content_type": "knowledge", "segment_type": "evidence", "segment_source": "file.md"}
    assert _is_pinned(seg) is False


# ---------------------------------------------------------------------------
# _build_diff_from_segments
# ---------------------------------------------------------------------------

def test_build_diff_correct_counts():
    """Diff should correctly partition segments into removed/compressed/retained."""
    segments = [
        {"segment_id": "s1", "actions": '["remove"]', "tokens_raw": 100, "tokens_after_tp": 0,
         "segment_type": "knowledge", "segment_source": "old.md", "content_type": "", "debug_ref": "Legacy block"},
        {"segment_id": "s2", "actions": '["compress"]', "tokens_raw": 400, "tokens_after_tp": 100,
         "segment_type": "knowledge", "segment_source": "main.md", "content_type": "", "debug_ref": "MasterPlaybook"},
        {"segment_id": "s3", "actions": "[]", "tokens_raw": 50, "tokens_after_tp": 50,
         "segment_type": "instruction", "segment_source": "system.md", "content_type": "pinned", "debug_ref": "ADR log"},
    ]
    diff = _build_diff_from_segments("trace-123", segments)
    assert len(diff.removed) == 1
    assert len(diff.compressed) == 1
    assert len(diff.retained) == 1
    assert diff.retained[0].pinned is True


def test_build_diff_compression_pct_calculated():
    """Compression percentage should be computed for compressed blocks."""
    segments = [
        {"segment_id": "s1", "actions": '["compress"]', "tokens_raw": 1000, "tokens_after_tp": 250,
         "segment_type": "knowledge", "segment_source": "file.md", "content_type": "", "debug_ref": "Big doc"},
    ]
    diff = _build_diff_from_segments("t1", segments)
    b = diff.compressed[0]
    assert b.compression_pct == pytest.approx(75.0, abs=0.1)  # 1 - 250/1000 = 0.75


def test_build_diff_empty_segments():
    diff = _build_diff_from_segments("t-empty", [])
    assert diff.total_blocks == 0
    assert diff.removed == []
    assert diff.compressed == []
    assert diff.retained == []


# ---------------------------------------------------------------------------
# print_diff
# ---------------------------------------------------------------------------

def test_print_diff_empty(capsys):
    diff = _empty_diff()
    print_diff(diff)
    captured = capsys.readouterr()
    assert "No context changes" in captured.out


def test_print_diff_shows_symbols(capsys):
    segments = [
        {"segment_id": "s1", "actions": '["remove"]', "tokens_raw": 100, "tokens_after_tp": 0,
         "segment_type": "knowledge", "segment_source": "", "content_type": "", "debug_ref": "Old cache"},
        {"segment_id": "s2", "actions": '["compress"]', "tokens_raw": 200, "tokens_after_tp": 60,
         "segment_type": "knowledge", "segment_source": "", "content_type": "", "debug_ref": "Playbook"},
        {"segment_id": "s3", "actions": "[]", "tokens_raw": 50, "tokens_after_tp": 50,
         "segment_type": "instruction", "segment_source": "", "content_type": "pinned", "debug_ref": "ADR"},
    ]
    diff = _build_diff_from_segments("trace-1", segments)
    print_diff(diff)
    out = capsys.readouterr().out
    assert "+ " in out  # removed symbol
    assert "~ " in out  # compressed symbol
    assert "= " in out  # retained symbol


def test_print_diff_pinned_in_retained(capsys):
    """Pinned blocks should show as retained with '=' symbol."""
    b = DiffBlock("x1", "Architecture ADR", "retained", pinned=True, tokens_before=100, tokens_after=100)
    diff = ContextDiff(trace_id="t", timestamp=None, removed=[], compressed=[], retained=[b])
    print_diff(diff)
    out = capsys.readouterr().out
    assert "= " in out
    assert "Pinned" in out or "RETAINED" in out


def test_print_diff_json_output(capsys):
    """`--json` output must be valid JSON with required keys."""
    segments = [
        {"segment_id": "s1", "actions": '["remove"]', "tokens_raw": 50, "tokens_after_tp": 0,
         "segment_type": "knowledge", "segment_source": "", "content_type": "", "debug_ref": "Old"},
    ]
    diff = _build_diff_from_segments("t-json", segments)
    print_diff(diff, raw=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "removed" in data
    assert "compressed" in data
    assert "retained" in data
    assert "summary" in data
    assert data["summary"]["removed"] == 1


# ---------------------------------------------------------------------------
# Pro gating
# ---------------------------------------------------------------------------

def test_diff_gated_non_pro(capsys):
    """Non-Pro license should print an upgrade prompt."""
    with patch("tokenpak.agent.license.activation.is_pro", return_value=False):
        args = MagicMock()
        args.verbose = False
        args.raw = False
        args.since = None
        run_diff_cmd(args)
    captured = capsys.readouterr()
    assert "Pro" in captured.out or "license" in captured.out.lower()


def test_diff_empty_when_no_trace(capsys):
    """No trace in DB → clean empty diff output."""
    with (
        patch("tokenpak.agent.license.activation.is_pro", return_value=True),
        patch("tokenpak.agent.cli.commands.diff._get_recent_trace", return_value=None),
    ):
        args = MagicMock()
        args.verbose = False
        args.raw = False
        args.json = False
        args.since = None
        run_diff_cmd(args)
    captured = capsys.readouterr()
    assert "No context changes" in captured.out
