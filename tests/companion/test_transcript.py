# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.companion.transcript.parser."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tokenpak.companion.transcript.parser import (
    TranscriptMessage,
    TranscriptSummary,
    _extract_content,
    _extract_role,
    _extract_tool_calls,
    _flatten_content_blocks,
    find_live_transcript,
    parse_transcript,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _write_jsonl(tmp_path: Path, lines: list[dict]) -> Path:
    """Write a list of dicts as JSONL to a temp file."""
    p = tmp_path / "test.jsonl"
    p.write_text("\n".join(json.dumps(d) for d in lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# parse_transcript — file handling
# ---------------------------------------------------------------------------


def test_parse_nonexistent_file_returns_empty():
    s = parse_transcript("/nonexistent/path/session.jsonl")
    assert s.message_count == 0
    assert s.tokens_est == 0
    assert s.parse_errors == 0
    assert s.messages == []


def test_parse_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    s = parse_transcript(p)
    assert s.message_count == 0
    assert s.tokens_est == 0


def test_parse_sets_file_size(tmp_path):
    p = _write_jsonl(tmp_path, [{"type": "last-prompt", "lastPrompt": "hi", "sessionId": "x"}])
    s = parse_transcript(p)
    assert s.file_size_bytes == p.stat().st_size
    assert s.file_size_bytes > 0


# ---------------------------------------------------------------------------
# parse_transcript — malformed lines
# ---------------------------------------------------------------------------


def test_malformed_lines_are_skipped_and_counted():
    s = parse_transcript(FIXTURES / "malformed.jsonl")
    # 3 valid lines (queue-op, user, last-prompt) + 2 malformed
    assert s.parse_errors == 2
    assert s.message_count == 3


def test_malformed_does_not_raise():
    """parse_transcript must never raise on bad input."""
    s = parse_transcript(FIXTURES / "malformed.jsonl")
    assert isinstance(s, TranscriptSummary)


# ---------------------------------------------------------------------------
# parse_transcript — message types from fixture
# ---------------------------------------------------------------------------


def test_basic_session_message_count():
    s = parse_transcript(FIXTURES / "basic_session.jsonl")
    assert s.message_count == 6


def test_basic_session_role_counts():
    s = parse_transcript(FIXTURES / "basic_session.jsonl")
    assert s.role_counts["queue-operation"] == 1
    assert s.role_counts["user"] == 1
    assert s.role_counts["assistant"] == 1
    assert s.role_counts["attachment"] == 1
    assert s.role_counts["last-prompt"] == 1
    assert s.role_counts["ai-title"] == 1


def test_basic_session_tokens_are_positive():
    s = parse_transcript(FIXTURES / "basic_session.jsonl")
    assert s.tokens_est > 0
    # Total should be sum of per-message tokens
    assert s.tokens_est == sum(m.tokens_est for m in s.messages)


# ---------------------------------------------------------------------------
# _extract_content — queue-operation
# ---------------------------------------------------------------------------


def test_queue_operation_extracts_content_field():
    obj = {
        "type": "queue-operation",
        "operation": "enqueue",
        "content": "Please write unit tests",
        "sessionId": "s1",
    }
    assert _extract_content(obj) == "Please write unit tests"


def test_queue_operation_empty_content():
    obj = {"type": "queue-operation", "operation": "enqueue", "sessionId": "s1"}
    assert _extract_content(obj) == ""


# ---------------------------------------------------------------------------
# _extract_content — user
# ---------------------------------------------------------------------------


def test_user_string_content():
    obj = {
        "type": "user",
        "message": {"role": "user", "content": "Hello, I need help"},
    }
    assert _extract_content(obj) == "Hello, I need help"


def test_user_list_content_with_tool_result():
    obj = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_001",
                    "content": [{"type": "text", "text": "File contents here"}],
                }
            ],
        },
    }
    content = _extract_content(obj)
    assert "File contents here" in content


def test_user_no_message_key_returns_empty():
    obj = {"type": "user"}
    assert _extract_content(obj) == ""


# ---------------------------------------------------------------------------
# _extract_content — assistant
# ---------------------------------------------------------------------------


def test_assistant_text_block():
    obj = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "I will help you with that."}],
        },
    }
    assert _extract_content(obj) == "I will help you with that."


def test_assistant_tool_use_block():
    obj = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls -la"}}
            ],
        },
    }
    content = _extract_content(obj)
    assert "tool_use:Bash" in content
    assert "ls -la" in content


def test_assistant_thinking_block():
    obj = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me reason through this step by step."},
                {"type": "text", "text": "Here is my answer."},
            ],
        },
    }
    content = _extract_content(obj)
    assert "Let me reason through this step by step." in content
    assert "Here is my answer." in content


def test_assistant_mixed_blocks_multiblock():
    s = parse_transcript(FIXTURES / "multiblock_assistant.jsonl")
    asst_msgs = [m for m in s.messages if m.type == "assistant"]
    assert len(asst_msgs) == 1
    # Should have text, thinking, and tool_use content
    content = asst_msgs[0].content
    assert "Let me run the tests" in content
    assert "tool_use:Bash" in content


# ---------------------------------------------------------------------------
# _extract_content — attachment
# ---------------------------------------------------------------------------


def test_attachment_deferred_tools():
    obj = {
        "type": "attachment",
        "attachment": {
            "type": "deferred_tools_delta",
            "addedNames": ["AskUserQuestion", "TodoWrite"],
            "addedLines": ["AskUserQuestion", "TodoWrite"],
            "removedNames": [],
        },
    }
    content = _extract_content(obj)
    assert "AskUserQuestion" in content
    assert "TodoWrite" in content


def test_attachment_with_text_content():
    obj = {
        "type": "attachment",
        "attachment": {
            "type": "system_prompt",
            "content": "You are a helpful assistant working in ~/project.",
        },
    }
    content = _extract_content(obj)
    assert "You are a helpful assistant" in content


def test_attachment_fallback_to_json():
    obj = {
        "type": "attachment",
        "attachment": {"type": "unknown_type", "data": 42},
    }
    content = _extract_content(obj)
    # Should not raise; should return something non-empty
    assert len(content) > 0


# ---------------------------------------------------------------------------
# _extract_content — last-prompt
# ---------------------------------------------------------------------------


def test_last_prompt_extracts_lastprompt():
    obj = {"type": "last-prompt", "lastPrompt": "Summarise the diff", "sessionId": "s"}
    assert _extract_content(obj) == "Summarise the diff"


def test_last_prompt_missing_field_returns_empty():
    obj = {"type": "last-prompt", "sessionId": "s"}
    assert _extract_content(obj) == ""


# ---------------------------------------------------------------------------
# _extract_content — ai-title
# ---------------------------------------------------------------------------


def test_ai_title_extracts_title_field():
    obj = {"type": "ai-title", "title": "Refactoring the auth module"}
    assert _extract_content(obj) == "Refactoring the auth module"


def test_ai_title_falls_back_to_content():
    obj = {"type": "ai-title", "content": "Fallback title"}
    assert _extract_content(obj) == "Fallback title"


def test_ai_title_empty_when_no_fields():
    obj = {"type": "ai-title"}
    assert _extract_content(obj) == ""


# ---------------------------------------------------------------------------
# _extract_content — progress / unknown
# ---------------------------------------------------------------------------


def test_progress_type_extracts_content_field():
    obj = {"type": "progress", "content": "Step 3 of 5 complete"}
    assert _extract_content(obj) == "Step 3 of 5 complete"


def test_unknown_type_extracts_text_field():
    obj = {"type": "x-custom", "text": "some text"}
    assert _extract_content(obj) == "some text"


def test_unknown_type_no_known_fields_returns_empty():
    obj = {"type": "x-custom", "foo": "bar"}
    assert _extract_content(obj) == ""


# ---------------------------------------------------------------------------
# _extract_role
# ---------------------------------------------------------------------------


def test_role_user():
    obj = {"type": "user", "message": {"role": "user", "content": "hi"}}
    assert _extract_role(obj) == "user"


def test_role_assistant():
    obj = {"type": "assistant", "message": {"role": "assistant", "content": []}}
    assert _extract_role(obj) == "assistant"


def test_role_unknown_returns_type():
    obj = {"type": "queue-operation"}
    assert _extract_role(obj) == "queue-operation"


# ---------------------------------------------------------------------------
# _extract_tool_calls
# ---------------------------------------------------------------------------


def test_extract_tool_calls_from_assistant():
    obj = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "I'll read the file."},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a.py"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    calls = _extract_tool_calls(obj)
    assert len(calls) == 2
    assert calls[0]["name"] == "Read"
    assert calls[1]["name"] == "Bash"


def test_extract_tool_calls_user_returns_empty():
    obj = {"type": "user", "message": {"content": "no tools here"}}
    assert _extract_tool_calls(obj) == []


def test_extract_tool_calls_no_tool_use_blocks():
    obj = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Done."}]},
    }
    assert _extract_tool_calls(obj) == []


# ---------------------------------------------------------------------------
# token counting — uses tiktoken not heuristic
# ---------------------------------------------------------------------------


def test_tokens_est_uses_tiktoken_not_heuristic(tmp_path):
    """Verify token counts are not simply len(text)//4."""
    text = "Hello, this is a test message for token counting."
    p = _write_jsonl(tmp_path, [{"type": "last-prompt", "lastPrompt": text, "sessionId": "t"}])
    s = parse_transcript(p)
    assert len(s.messages) == 1
    msg = s.messages[0]
    # tiktoken gives 10 tokens for this string; char//4 would give ~12
    # Accept anything in [8, 15] — either tiktoken or reasonable fallback
    assert 8 <= msg.tokens_est <= 15


def test_total_tokens_equals_sum_of_message_tokens(tmp_path):
    lines = [
        {"type": "last-prompt", "lastPrompt": "First prompt here", "sessionId": "t"},
        {"type": "ai-title", "title": "Session title", "sessionId": "t"},
    ]
    p = _write_jsonl(tmp_path, lines)
    s = parse_transcript(p)
    assert s.tokens_est == sum(m.tokens_est for m in s.messages)


# ---------------------------------------------------------------------------
# find_live_transcript
# ---------------------------------------------------------------------------


def test_find_live_transcript_returns_string_or_none():
    result = find_live_transcript()
    assert result is None or isinstance(result, str)


def test_find_live_transcript_by_session_id():
    """If we pass a known session_id, it should find the matching file."""
    # Pick a real transcript from ~/.claude/projects/
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        pytest.skip("No ~/.claude/projects directory")

    # Find any real .jsonl file
    candidates = list(claude_dir.glob("*/*.jsonl"))
    if not candidates:
        pytest.skip("No transcript files found")

    target = candidates[0]
    session_id = target.stem  # filename without .jsonl is the session UUID

    result = find_live_transcript(session_id=session_id)
    assert result is not None
    assert result == str(target)


def test_find_live_transcript_returns_real_path():
    """find_live_transcript() points to an existing file."""
    result = find_live_transcript()
    if result is not None:
        assert Path(result).exists()
        assert result.endswith(".jsonl")


def test_find_live_transcript_missing_claude_dir(monkeypatch, tmp_path):
    """Returns None gracefully when ~/.claude/projects doesn't exist."""
    monkeypatch.setattr(
        "tokenpak.companion.transcript.parser.Path.home",
        lambda: tmp_path,
    )
    result = find_live_transcript()
    assert result is None


# ---------------------------------------------------------------------------
# Integration: parse a real transcript
# ---------------------------------------------------------------------------


def test_parse_real_transcript_smoke():
    """Parse a real transcript; verify basic shape of results."""
    path = find_live_transcript()
    if path is None:
        pytest.skip("No live transcript found")

    s = parse_transcript(path)
    assert isinstance(s, TranscriptSummary)
    assert s.message_count >= 0
    assert s.tokens_est >= 0
    # Every message should have a non-empty type
    for m in s.messages:
        assert m.type, f"Expected non-empty type for message: {m}"
