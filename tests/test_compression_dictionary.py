"""
Tests for CompressionDictionary — project-specific phrase replacement.

Covers:
  1. Basic replacement from in-memory dictionary
  2. Case-insensitive mode
  3. Longer phrases take priority over shorter overlapping ones
  4. Non-string content is passed through unchanged
  5. Auto-learn: frequent phrases appear in suggestions after threshold
  6. suggest_entries() returns the correct format
  7. save_suggestions() writes to disk and skips duplicates
  8. Missing / malformed dict file is handled gracefully (no crash)
  9. DictionaryResult statistics (replacements_made, tokens_saved_est)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tokenpak.agent.compression.dictionary import (
    CompressionDictionary,
    DictionaryResult,
    SuggestedEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msgs(*texts):
    return [{"role": "user", "content": t} for t in texts]


def _dict_with(tmp_path: Path, entries: dict) -> Path:
    p = tmp_path / "compression_dict.json"
    p.write_text(json.dumps(entries))
    return p


# ---------------------------------------------------------------------------
# Test 1 — Basic replacement
# ---------------------------------------------------------------------------

def test_basic_replacement(tmp_path):
    """Phrases in the dictionary are replaced with their tokens."""
    d = _dict_with(tmp_path, {
        "environment variable configuration mismatch": "ENV_MISMATCH",
        "authentication token expired": "AUTH_EXPIRED",
    })
    comp = CompressionDictionary(dict_path=d)
    result = comp.apply(_msgs(
        "There was an environment variable configuration mismatch in the pipeline.",
        "The user got an authentication token expired error.",
    ))
    assert result.replacements_made == 2
    assert "ENV_MISMATCH" in result.messages[0]["content"]
    assert "environment variable configuration mismatch" not in result.messages[0]["content"]
    assert "AUTH_EXPIRED" in result.messages[1]["content"]
    assert result.applied_entries == {
        "environment variable configuration mismatch": "ENV_MISMATCH",
        "authentication token expired": "AUTH_EXPIRED",
    }


# ---------------------------------------------------------------------------
# Test 2 — Case-insensitive mode
# ---------------------------------------------------------------------------

def test_case_insensitive(tmp_path):
    """case_sensitive=False replaces regardless of capitalisation."""
    d = _dict_with(tmp_path, {"connection refused": "CONN_REFUSED"})
    comp = CompressionDictionary(dict_path=d, case_sensitive=False)
    result = comp.apply(_msgs("We saw a Connection Refused error."))
    assert result.replacements_made == 1
    assert "CONN_REFUSED" in result.messages[0]["content"]


# ---------------------------------------------------------------------------
# Test 3 — Longer phrases win over shorter overlaps
# ---------------------------------------------------------------------------

def test_longer_phrase_priority(tmp_path):
    """When two phrases overlap, the longer one should be applied first."""
    d = _dict_with(tmp_path, {
        "connection refused": "CONN_REFUSED",
        "connection refused by remote host": "CONN_REFUSED_REMOTE",
    })
    comp = CompressionDictionary(dict_path=d)
    result = comp.apply(_msgs("Got a connection refused by remote host signal."))
    assert "CONN_REFUSED_REMOTE" in result.messages[0]["content"]
    # The shorter phrase should NOT be in the output after the longer was applied
    assert "connection refused by remote host" not in result.messages[0]["content"]


# ---------------------------------------------------------------------------
# Test 4 — Non-string content passes through unchanged
# ---------------------------------------------------------------------------

def test_non_string_content(tmp_path):
    """Messages with non-string content (None, list) are not modified."""
    d = _dict_with(tmp_path, {"foo bar baz qux quux": "FBBQQ"})
    comp = CompressionDictionary(dict_path=d)
    msg_none = {"role": "user", "content": None}
    msg_list = {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    result = comp.apply([msg_none, msg_list])
    assert result.messages[0]["content"] is None
    assert isinstance(result.messages[1]["content"], list)
    assert result.replacements_made == 0


# ---------------------------------------------------------------------------
# Test 5 — Auto-learn threshold
# ---------------------------------------------------------------------------

def test_autolearn_threshold(tmp_path):
    """
    Phrases repeated at or above threshold appear in suggestions;
    below threshold they do not.
    """
    d = _dict_with(tmp_path, {})  # empty dictionary
    comp = CompressionDictionary(
        dict_path=d,
        auto_learn_threshold=3,
        auto_learn_min_length=30,
    )
    # A long repeated phrase
    phrase = "the proxy service encountered an unexpected timeout error"

    # 2 occurrences — below threshold
    comp.apply(_msgs(phrase, phrase))
    assert not any(s.phrase == phrase for s in comp.suggestions), \
        "Phrase with 2 occurrences should NOT appear in suggestions"

    # 1 more → hits threshold
    comp.apply(_msgs(phrase))
    phrases_in_suggestions = [s.phrase for s in comp.suggestions]
    assert phrase in phrases_in_suggestions, \
        "Phrase with 3 occurrences should appear in suggestions"


# ---------------------------------------------------------------------------
# Test 6 — suggest_entries format
# ---------------------------------------------------------------------------

def test_suggest_entries_format(tmp_path):
    """suggest_entries() returns a list of {phrase: token} dicts."""
    d = _dict_with(tmp_path, {})
    comp = CompressionDictionary(dict_path=d, auto_learn_threshold=2, auto_learn_min_length=20)
    phrase = "authentication service unavailable on startup"
    comp.apply(_msgs(phrase, phrase))

    entries = comp.suggest_entries()
    assert isinstance(entries, list)
    assert len(entries) >= 1
    entry = next(e for e in entries if phrase in e)
    assert isinstance(entry[phrase], str)
    assert len(entry[phrase]) > 0


# ---------------------------------------------------------------------------
# Test 7 — save_suggestions writes to disk, skips duplicates
# ---------------------------------------------------------------------------

def test_save_suggestions_writes_and_dedupes(tmp_path):
    """save_suggestions() appends new entries and skips existing ones."""
    d = _dict_with(tmp_path, {"existing phrase that is long enough for test": "EXIST"})
    comp = CompressionDictionary(dict_path=d, auto_learn_threshold=2, auto_learn_min_length=20)
    phrase = "the gateway service failed to initialize correctly"
    comp.apply(_msgs(phrase, phrase))

    added = comp.save_suggestions()
    assert added >= 1

    # Reload and verify
    saved = json.loads(d.read_text())
    assert phrase in saved

    # Calling again adds 0 new (already persisted)
    comp2 = CompressionDictionary(dict_path=d, auto_learn_threshold=2, auto_learn_min_length=20)
    comp2.apply(_msgs(phrase, phrase))
    added2 = comp2.save_suggestions()
    assert added2 == 0


# ---------------------------------------------------------------------------
# Test 8 — Missing / malformed dict file is handled gracefully
# ---------------------------------------------------------------------------

def test_missing_dict_file(tmp_path):
    """If the dict file doesn't exist, apply() still works (no replacements)."""
    missing = tmp_path / "nonexistent.json"
    comp = CompressionDictionary(dict_path=missing)
    result = comp.apply(_msgs("some message with no replacements"))
    assert result.replacements_made == 0
    assert result.messages[0]["content"] == "some message with no replacements"


def test_malformed_dict_file(tmp_path):
    """If the dict file is invalid JSON, it is treated as empty."""
    d = tmp_path / "compression_dict.json"
    d.write_text("NOT VALID JSON {{}")
    comp = CompressionDictionary(dict_path=d)
    result = comp.apply(_msgs("some text"))
    assert result.replacements_made == 0


# ---------------------------------------------------------------------------
# Test 9 — DictionaryResult statistics
# ---------------------------------------------------------------------------

def test_result_statistics(tmp_path):
    """tokens_saved_est should be positive when long phrases are replaced."""
    long_phrase = "environment variable configuration mismatch"  # 44 chars
    short_token = "ENV_MISMATCH"                                  # 12 chars
    d = _dict_with(tmp_path, {long_phrase: short_token})
    comp = CompressionDictionary(dict_path=d)

    text = f"Encountered {long_phrase} twice and {long_phrase} again."
    result = comp.apply(_msgs(text))

    assert result.replacements_made == 2
    expected_char_savings = (len(long_phrase) - len(short_token)) * 2  # 64 chars
    assert result.tokens_saved_est == expected_char_savings // 4
