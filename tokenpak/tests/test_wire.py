"""Unit tests for tokenpak.wire module."""

import pytest
from tokenpak.wire import make_slice_id, pack


class TestMakeSliceId:
    """Tests for make_slice_id function."""

    def test_basic_slice_id_generation(self):
        """Should generate a slice_id from content and ref."""
        result = make_slice_id("hello world", "test/file.py")
        assert result.startswith("s_")
        assert len(result) == 10  # s_ + 8 hex chars

    def test_slice_id_deterministic(self):
        """Same content + ref should produce same slice_id."""
        content = "some content"
        ref = "path/to/file"
        id1 = make_slice_id(content, ref)
        id2 = make_slice_id(content, ref)
        assert id1 == id2

    def test_slice_id_unique_on_content_change(self):
        """Different content should produce different slice_id."""
        ref = "same/ref"
        id1 = make_slice_id("content1", ref)
        id2 = make_slice_id("content2", ref)
        assert id1 != id2

    def test_slice_id_unique_on_ref_change(self):
        """Different ref should produce different slice_id."""
        content = "same content"
        id1 = make_slice_id(content, "ref1")
        id2 = make_slice_id(content, "ref2")
        assert id1 != id2

    def test_slice_id_empty_content(self):
        """Should handle empty content."""
        result = make_slice_id("", "ref")
        assert result.startswith("s_")
        assert len(result) == 10

    def test_slice_id_empty_ref(self):
        """Should handle empty ref."""
        result = make_slice_id("content", "")
        assert result.startswith("s_")
        assert len(result) == 10


class TestPack:
    """Tests for pack function."""

    def test_pack_empty_blocks(self):
        """Should handle empty block list."""
        result = pack([], 1000)
        assert "TOKPAK:1" in result
        assert "BUDGET: {max:1000, used:0}" in result
        assert "BLOCKS: 0" in result
        assert result.endswith("---")

    def test_pack_basic_single_block(self):
        """Should pack a single block with basic fields."""
        blocks = [
            {
                "ref": "test/file.py",
                "type": "code",
                "quality": 0.95,
                "tokens": 100,
                "content": "def hello():\n    return 'world'",
            }
        ]
        result = pack(blocks, 1000)
        assert "TOKPAK:1" in result
        assert "BLOCKS: 1" in result
        assert "BUDGET: {max:1000, used:100}" in result
        assert "[REF: test/file.py]" in result
        assert "[TYPE: code]" in result
        assert "[QUALITY: 0.95]" in result
        assert "[TOKENS: 100]" in result
        assert "[SLICE: s_" in result
        assert "def hello():" in result

    def test_pack_multiple_blocks(self):
        """Should pack multiple blocks."""
        blocks = [
            {
                "ref": "file1.py",
                "type": "code",
                "quality": 0.9,
                "tokens": 50,
                "content": "# file 1",
            },
            {
                "ref": "file2.py",
                "type": "docs",
                "quality": 0.8,
                "tokens": 75,
                "content": "# file 2",
            },
        ]
        result = pack(blocks, 200)
        assert "BLOCKS: 2" in result
        assert "BUDGET: {max:200, used:125}" in result
        assert "[REF: file1.py]" in result
        assert "[REF: file2.py]" in result

    def test_pack_with_metadata(self):
        """Should include metadata in output."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "test",
            }
        ]
        metadata = {"source": "vault", "version": "1.0"}
        result = pack(blocks, 100, metadata)
        assert "META: source=vault, version=1.0" in result

    def test_pack_quality_formatting(self):
        """Quality should be formatted to 2 decimal places."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 0.12345,
                "tokens": 10,
                "content": "test",
            }
        ]
        result = pack(blocks, 100)
        assert "[QUALITY: 0.12]" in result

    def test_pack_missing_optional_fields(self):
        """Should handle blocks missing optional fields."""
        blocks = [
            {
                "ref": "test.py",
                "content": "test content",
                # type, quality, tokens omitted
            }
        ]
        result = pack(blocks, 100)
        assert "[TYPE: unknown]" in result
        assert "[QUALITY: 1.00]" in result
        assert "[TOKENS: 0]" in result
        assert "BUDGET: {max:100, used:0}" in result

    def test_pack_with_explicit_slice_id(self):
        """Should use explicit slice_id if provided."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "test",
                "slice_id": "s_custom123",
            }
        ]
        result = pack(blocks, 100)
        assert "[SLICE: s_custom123]" in result

    def test_pack_content_whitespace_stripped(self):
        """Content should be stripped of leading/trailing whitespace."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "  \n  test content  \n  ",
            }
        ]
        result = pack(blocks, 100)
        assert "test content" in result
        # Should not have the extra whitespace in the output
        lines = result.split("\n")
        content_line = [l for l in lines if "test content" in l][0]
        assert content_line == "test content"

    def test_pack_with_provenance_object(self):
        """Should handle provenance as object with attributes."""

        class MockProvenance:
            source_type = "vault"
            source_id = "file123"
            source_version = "abc123def456"

        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "test",
                "provenance": MockProvenance(),
            }
        ]
        result = pack(blocks, 100)
        assert "[SOURCE: vault:file123]" in result
        assert "[VERSION: abc123def456]" in result

    def test_pack_with_provenance_dict(self):
        """Should handle provenance as dictionary."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "test",
                "provenance": {
                    "source_type": "github",
                    "source_id": "repo456",
                    "source_version": "v1.2.3",
                },
            }
        ]
        result = pack(blocks, 100)
        assert "[SOURCE: github:repo456]" in result
        assert "[VERSION: v1.2.3]" in result

    def test_pack_with_partial_provenance(self):
        """Should handle incomplete provenance data."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "test",
                "provenance": {"source_type": "vault"},
            }
        ]
        result = pack(blocks, 100)
        # Should not include SOURCE line without both source_type and source_id
        assert "[SOURCE:" not in result

    def test_pack_wire_format_structure(self):
        """Should produce correct wire format structure."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "content",
            }
        ]
        result = pack(blocks, 100)
        lines = result.split("\n")
        assert lines[0] == "TOKPAK:1"
        assert lines[1].startswith("BUDGET:")
        assert lines[2].startswith("BLOCKS:")
        # Block should start with --- separator
        separators = [i for i, l in enumerate(lines) if l == "---"]
        assert len(separators) >= 2  # At least opening and closing
        block_start = separators[0]
        assert lines[block_start + 1].startswith("[REF:")
        # Last line should be ---
        assert lines[-1] == "---"

    def test_pack_negative_budget(self):
        """Should handle negative budget (edge case)."""
        blocks = []
        result = pack(blocks, -100)
        assert "BUDGET: {max:-100, used:0}" in result

    def test_pack_zero_budget(self):
        """Should handle zero budget."""
        blocks = []
        result = pack(blocks, 0)
        assert "BUDGET: {max:0, used:0}" in result

    def test_pack_large_token_count(self):
        """Should handle large token counts."""
        blocks = [
            {
                "ref": "test.py",
                "type": "code",
                "quality": 1.0,
                "tokens": 999999,
                "content": "test",
            }
        ]
        result = pack(blocks, 1000000)
        assert "BUDGET: {max:1000000, used:999999}" in result
        assert "[TOKENS: 999999]" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
