# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.assembler — CanonBlockRegistry + ContextAssembler."""

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.assembler import CanonBlockRegistry, ContextAssembler


# ─── CanonBlockRegistry ───────────────────────────────────────────────────────


class TestCanonBlockRegistry:
    """Tests for CanonBlockRegistry."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry = CanonBlockRegistry(base_dir=self.tmpdir)

    def test_new_block_is_new(self):
        """First registration returns v1 and is_new=True."""
        version, is_new = self.registry.get_or_register("SOUL", "hello world")
        assert version == "v1"
        assert is_new is True

    def test_same_content_not_new(self):
        """Same content on second call returns same version and is_new=False."""
        self.registry.get_or_register("SOUL", "hello world")
        version, is_new = self.registry.get_or_register("SOUL", "hello world")
        assert version == "v1"
        assert is_new is False

    def test_changed_content_bumps_version(self):
        """Changed content bumps version and returns is_new=True."""
        self.registry.get_or_register("SOUL", "hello world")
        version, is_new = self.registry.get_or_register("SOUL", "new content")
        assert version == "v2"
        assert is_new is True

    def test_multiple_blocks_independent(self):
        """Different block_ids are tracked independently."""
        v1, _ = self.registry.get_or_register("SOUL", "soul content")
        v2, _ = self.registry.get_or_register("TOOLS", "tools content")
        assert v1 == "v1"
        assert v2 == "v1"

    def test_current_version_unknown_block(self):
        """current_version returns None for unknown block_id."""
        assert self.registry.current_version("UNKNOWN") is None

    def test_current_version_known_block(self):
        """current_version returns correct version after registration."""
        self.registry.get_or_register("SOUL", "hello")
        assert self.registry.current_version("SOUL") == "v1"
        self.registry.get_or_register("SOUL", "changed")
        assert self.registry.current_version("SOUL") == "v2"

    def test_read_block_content_roundtrip(self):
        """Block content can be read back after registration."""
        content = "This is soul content\nMultiple lines"
        version, _ = self.registry.get_or_register("SOUL", content)
        result = self.registry.read_block_content("SOUL", version)
        assert result == content

    def test_read_block_content_missing(self):
        """read_block_content returns None for unknown block/version."""
        assert self.registry.read_block_content("GHOST", "v99") is None

    def test_manifest_persists_across_instances(self):
        """Manifest is persisted so a new registry instance sees same state."""
        self.registry.get_or_register("SOUL", "persistent content")
        # New instance, same base_dir
        registry2 = CanonBlockRegistry(base_dir=self.tmpdir)
        version, is_new = registry2.get_or_register("SOUL", "persistent content")
        assert version == "v1"
        assert is_new is False

    def test_empty_content_allowed(self):
        """Empty string is a valid block content."""
        version, is_new = self.registry.get_or_register("EMPTY", "")
        assert version == "v1"
        assert is_new is True

    def test_content_hash_stable(self):
        """Same content always produces same hash (deterministic)."""
        _, _ = self.registry.get_or_register("X", "stable")
        _, _ = self.registry.get_or_register("X", "stable")
        entry = self.registry._manifest["X"]
        h1 = entry["hash"]

        registry2 = CanonBlockRegistry(base_dir=tempfile.mkdtemp())
        registry2.get_or_register("X", "stable")
        h2 = registry2._manifest["X"]["hash"]
        assert h1 == h2


# ─── ContextAssembler ────────────────────────────────────────────────────────


class TestContextAssembler:
    """Tests for ContextAssembler."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.assembler = ContextAssembler(session_id="test-session", base_dir=self.tmpdir)

    def test_add_canon_block_first_call_inlines(self):
        """First call for a block returns inline format."""
        result = self.assembler.add_canon_block("SOUL", "soul content")
        assert result == "SOUL=soul content"

    def test_add_canon_block_second_call_references(self):
        """Second call for same block+version returns reference format."""
        self.assembler.add_canon_block("SOUL", "soul content")
        result = self.assembler.add_canon_block("SOUL", "soul content")
        assert result.startswith("SOUL=@SOUL#v")

    def test_add_canon_block_version_change_reinlines(self):
        """Changed content causes re-inline on next call."""
        self.assembler.add_canon_block("SOUL", "v1 content")
        result = self.assembler.add_canon_block("SOUL", "v2 content")
        assert result == "SOUL=v2 content"

    def test_assemble_context_empty(self):
        """assemble_context with empty dict returns 'CANON:'."""
        result = self.assembler.assemble_context({})
        assert result == "CANON:"

    def test_assemble_context_single_block(self):
        """assemble_context with one block produces correct CANON section."""
        result = self.assembler.assemble_context({"SOUL": ("content", None)})
        assert result.startswith("CANON:")
        assert "SOUL=content" in result

    def test_assemble_context_multiple_blocks(self):
        """assemble_context with multiple blocks includes all entries."""
        result = self.assembler.assemble_context({
            "SOUL": ("soul text", None),
            "TOOLS": ("tools text", None),
        })
        assert "SOUL=soul text" in result
        assert "TOOLS=tools text" in result

    def test_assemble_context_second_turn_uses_refs(self):
        """Second call with same content uses @ref format."""
        self.assembler.assemble_context({"SOUL": ("soul text", None)})
        result = self.assembler.assemble_context({"SOUL": ("soul text", None)})
        assert "@SOUL#" in result

    def test_session_persists_across_instances(self):
        """Session state persists so a new assembler instance sees sent blocks."""
        self.assembler.assemble_context({"SOUL": ("soul text", None)}, save_session=True)
        # New assembler, same session_id + base_dir
        assembler2 = ContextAssembler(session_id="test-session", base_dir=self.tmpdir)
        result = assembler2.assemble_context({"SOUL": ("soul text", None)})
        assert "@SOUL#" in result  # should reference, not inline

    def test_sent_blocks_property(self):
        """sent_blocks tracks which blocks have been sent."""
        assert self.assembler.sent_blocks == {}
        self.assembler.add_canon_block("SOUL", "content")
        assert "SOUL" in self.assembler.sent_blocks

    def test_session_id_stored(self):
        """session_id is accessible on the assembler instance."""
        assert self.assembler.session_id == "test-session"

    def test_different_sessions_isolated(self):
        """Two different session_ids do not share sent_blocks state."""
        a1 = ContextAssembler(session_id="sess-A", base_dir=self.tmpdir)
        a2 = ContextAssembler(session_id="sess-B", base_dir=self.tmpdir)
        a1.add_canon_block("SOUL", "shared content")
        # sess-B has never sent SOUL — should still inline
        result = a2.add_canon_block("SOUL", "shared content")
        assert result == "SOUL=shared content"

    def test_save_session_false_does_not_persist(self):
        """save_session=False means next assembler instance won't see sent blocks."""
        self.assembler.assemble_context({"SOUL": ("soul text", None)}, save_session=False)
        assembler2 = ContextAssembler(session_id="test-session", base_dir=self.tmpdir)
        result = assembler2.assemble_context({"SOUL": ("soul text", None)})
        # Should inline since session was not saved
        assert "SOUL=soul text" in result
