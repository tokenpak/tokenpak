"""
Unit tests for canon_session.py — Canon bug fix verification.

Tests that apply_canon_refs() handles BOTH:
  - system as str (original path)
  - system as list-of-content-blocks (Anthropic cache_control format)

The bug: `isinstance(system, str)` check caused 0 hits after vault injection
because vault injection always converts system to a list.
"""
import json
import sys
import os

# Ensure .ocp directory is on the path so we can import canon_session
OCP_DIR = os.path.expanduser("~/.openclaw/workspace/.ocp")
if OCP_DIR not in sys.path:
    sys.path.insert(0, OCP_DIR)

import tempfile
import pytest
from pathlib import Path

# Patch STATE_DIR before import so tests don't pollute ~/.openclaw
with tempfile.TemporaryDirectory() as _td:
    _TEST_STATE_DIR = _td

# We need to import and monkey-patch STATE_DIR
import canon_session as cs


def _make_body(system, messages=None):
    """Helper: build a minimal Anthropic-format request body."""
    data = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "system": system,
        "messages": messages or [{"role": "user", "content": "hello"}],
    }
    return json.dumps(data).encode()


CANON_TAG = cs.CANON_TAG
FAKE_BLOCK_CONTENT = "A" * 1000   # > MIN_BLOCK_TOKENS (200 * 4 chars ≈ 800 chars)


def _injection_section():
    return (
        f"{CANON_TAG}\n"
        f"--- [SOUL.md] (relevance: 0.9) ---\n"
        f"{FAKE_BLOCK_CONTENT}\n"
    )


class TestCanonStrSystem:
    """Verify original str-type system path still works."""

    def test_first_call_no_reference(self, tmp_path):
        """First call: block is new → no reference, returns unchanged tokens."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        system = f"You are helpful.\n{_injection_section()}"
        body = _make_body(system)
        new_body, refs, saved = cs.apply_canon_refs(body, session_id="test-str-1")

        assert refs == 0, "First call should not reference any blocks"
        assert saved == 0

    def test_second_call_produces_reference(self, tmp_path):
        """Second call with same content → reference returned."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        system = f"You are helpful.\n{_injection_section()}"
        body = _make_body(system)
        sid = "test-str-2"

        # First call — registers block
        cs.apply_canon_refs(body, session_id=sid)
        # Second call — same block → should reference
        new_body, refs, saved = cs.apply_canon_refs(body, session_id=sid)

        assert refs == 1, f"Second call should reference 1 block, got {refs}"
        assert saved > 0, "Should report tokens saved"

        # Verify the block was actually replaced in the output
        data = json.loads(new_body)
        assert isinstance(data["system"], str)
        assert "[REF:SOUL#v1]" in data["system"], "Should contain reference marker"
        assert FAKE_BLOCK_CONTENT not in data["system"], "Full content should be replaced"


class TestCanonListSystem:
    """Verify the bug fix: list-type system (after vault injection) now works."""

    def test_first_call_no_reference_list(self, tmp_path):
        """First call with list system: block is new → no reference."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        injection_text = _injection_section()
        system_list = [
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": injection_text, "cache_control": {"type": "ephemeral"}},
        ]
        body = _make_body(system_list)
        new_body, refs, saved = cs.apply_canon_refs(body, session_id="test-list-1")

        assert refs == 0, "First call should not reference any blocks"
        assert saved == 0

    def test_second_call_produces_reference_list(self, tmp_path):
        """Second call with list system: same block → reference injected in-place."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        injection_text = _injection_section()
        system_list = [
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": injection_text, "cache_control": {"type": "ephemeral"}},
        ]
        body = _make_body(system_list)
        sid = "test-list-2"

        # First call — registers block
        cs.apply_canon_refs(body, session_id=sid)
        # Second call — same block → should reference
        new_body, refs, saved = cs.apply_canon_refs(body, session_id=sid)

        assert refs == 1, f"Second call should reference 1 block, got {refs}"
        assert saved > 0, "Should report tokens saved"

        # Verify system is still a list (format preserved)
        data = json.loads(new_body)
        assert isinstance(data["system"], list), "System should remain a list"
        # The injection block should be updated
        injection_block = data["system"][1]
        assert "[REF:SOUL#v1]" in injection_block["text"], "Should contain reference marker"
        assert FAKE_BLOCK_CONTENT not in injection_block["text"], "Full content should be replaced"
        # cache_control should be preserved
        assert injection_block.get("cache_control") == {"type": "ephemeral"}, "cache_control should be preserved"

    def test_non_injection_blocks_unchanged(self, tmp_path):
        """Non-injection blocks in the list should not be modified."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        injection_text = _injection_section()
        system_list = [
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": injection_text, "cache_control": {"type": "ephemeral"}},
        ]
        body = _make_body(system_list)
        sid = "test-list-3"

        cs.apply_canon_refs(body, session_id=sid)
        new_body, refs, _ = cs.apply_canon_refs(body, session_id=sid)

        data = json.loads(new_body)
        # First block should be untouched
        assert data["system"][0]["text"] == "You are helpful.", "Non-injection block should be unchanged"

    def test_no_canon_tag_returns_unchanged(self, tmp_path):
        """If CANON_TAG not in any block, returns original body unchanged."""
        cs.STATE_DIR = tmp_path
        cs._SESSIONS.clear()

        system_list = [
            {"type": "text", "text": "Just a regular system prompt."},
        ]
        body = _make_body(system_list)
        new_body, refs, saved = cs.apply_canon_refs(body, session_id="test-notag")

        assert refs == 0
        assert saved == 0
        assert new_body == body, "Body should be unchanged when no CANON_TAG"


if __name__ == "__main__":
    # Quick smoke test without pytest
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        print("Testing str system path...")
        cs.STATE_DIR = td_path
        cs._SESSIONS.clear()
        system = f"You are helpful.\n{_injection_section()}"
        body = _make_body(system)
        sid = "smoke-str"
        _, refs1, _ = cs.apply_canon_refs(body, session_id=sid)
        _, refs2, saved2 = cs.apply_canon_refs(body, session_id=sid)
        assert refs1 == 0, f"First call refs should be 0, got {refs1}"
        assert refs2 == 1, f"Second call refs should be 1, got {refs2}"
        print(f"  ✅ str path: turn1={refs1} refs, turn2={refs2} refs, saved={saved2} tokens")

        print("Testing list system path (the bug fix)...")
        cs.STATE_DIR = td_path
        cs._SESSIONS.clear()
        injection_text = _injection_section()
        system_list = [
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": injection_text, "cache_control": {"type": "ephemeral"}},
        ]
        body = _make_body(system_list)
        sid = "smoke-list"
        _, refs1, _ = cs.apply_canon_refs(body, session_id=sid)
        new_body, refs2, saved2 = cs.apply_canon_refs(body, session_id=sid)
        assert refs1 == 0, f"First call refs should be 0, got {refs1}"
        assert refs2 == 1, f"Second call refs should be 1, got {refs2}"
        data = json.loads(new_body)
        assert isinstance(data["system"], list), "System should remain list"
        assert "[REF:SOUL#v1]" in data["system"][1]["text"]
        print(f"  ✅ list path: turn1={refs1} refs, turn2={refs2} refs, saved={saved2} tokens")
        print("All smoke tests passed!")
