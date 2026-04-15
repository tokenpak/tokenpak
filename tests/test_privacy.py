"""Tests for tokenpak._internal.fingerprint.privacy module."""


import pytest
pytest.importorskip("tokenpak._internal.fingerprint.privacy", reason="module not available in current build")
import pytest
from tokenpak._internal.fingerprint.privacy import apply_privacy, PrivacyLevel


class TestPrivacyLevel:
    """Test PrivacyLevel enum."""

    def test_privacy_levels_exist(self):
        """Test privacy level constants exist."""
        assert hasattr(PrivacyLevel, 'MINIMAL')
        assert hasattr(PrivacyLevel, 'STANDARD')
        assert hasattr(PrivacyLevel, 'FULL')

    def test_privacy_level_is_enum(self):
        """Test that PrivacyLevel is an enum."""
        from enum import Enum
        assert issubclass(PrivacyLevel, Enum)

    def test_privacy_level_count(self):
        """Test that there are exactly 3 privacy levels."""
        levels = list(PrivacyLevel)
        assert len(levels) == 3

    def test_privacy_level_values(self):
        """Test privacy level values."""
        assert PrivacyLevel.MINIMAL.value == "minimal"
        assert PrivacyLevel.STANDARD.value == "standard"
        assert PrivacyLevel.FULL.value == "full"


class TestApplyPrivacyBasic:
    """Test apply_privacy function basics."""

    def test_apply_privacy_minimal(self):
        """Test privacy with MINIMAL level."""
        fingerprint = {
            "fingerprint_id": "abc123",
            "total_tokens": 100,
            "segments": [{"type": "text", "content_hash": "xyz"}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)
        assert "fingerprint_id" in result

    def test_apply_privacy_standard(self):
        """Test privacy with STANDARD level."""
        fingerprint = {
            "fingerprint_id": "abc123",
            "total_tokens": 100,
            "segments": [{"type": "text"}, {"type": "code"}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert isinstance(result, dict)
        assert "segment_type_distribution" in result

    def test_apply_privacy_full(self):
        """Test privacy with FULL level."""
        fingerprint = {
            "fingerprint_id": "abc123",
            "total_tokens": 100,
            "segments": [{"type": "text", "hash": "123"}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.FULL)
        assert isinstance(result, dict)
        # FULL returns copy of original
        assert result == fingerprint

    def test_empty_fingerprint(self):
        """Test privacy on empty fingerprint."""
        result = apply_privacy({}, PrivacyLevel.FULL)
        assert isinstance(result, dict)

    def test_minimal_fingerprint(self):
        """Test with minimal fingerprint structure."""
        fingerprint = {"fingerprint_id": "test"}
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)


class TestApplyPrivacyMinimal:
    """Test MINIMAL privacy level."""

    def test_minimal_preserves_id(self):
        """Test MINIMAL preserves fingerprint_id."""
        fingerprint = {
            "fingerprint_id": "id123",
            "total_tokens": 50,
            "segments": [{"type": "text"}, {"type": "code"}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert result["fingerprint_id"] == "id123"

    def test_minimal_preserves_counts(self):
        """Test MINIMAL preserves token and segment counts."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 200,
            "segment_count": 5,
            "segments": [],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert result["total_tokens"] == 200
        assert result["segment_count"] == 5

    def test_minimal_removes_details(self):
        """Test MINIMAL removes segment details."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [{"type": "text", "content_hash": "xyz", "offset": 0}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        # Should not have segment_type_distribution (that's STANDARD)
        assert "segments" not in result or result.get("segments") is None


class TestApplyPrivacyStandard:
    """Test STANDARD privacy level."""

    def test_standard_includes_type_distribution(self):
        """Test STANDARD includes segment type distribution."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [
                {"type": "text"},
                {"type": "text"},
                {"type": "code"},
            ],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in result
        assert result["segment_type_distribution"]["text"] == 2
        assert result["segment_type_distribution"]["code"] == 1

    def test_standard_handles_missing_type(self):
        """Test STANDARD handles segments without type."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [
                {"type": "text"},
                {},  # missing type
            ],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in result
        assert result["segment_type_distribution"].get("unknown", 0) >= 1

    def test_standard_empty_segments(self):
        """Test STANDARD with empty segments list."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in result
        assert result["segment_type_distribution"] == {}


class TestApplyPrivacyFull:
    """Test FULL privacy level."""

    def test_full_preserves_all(self):
        """Test FULL preserves everything."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [{"type": "text", "hash": "abc", "secret": "data"}],
            "custom_field": "value",
        }
        result = apply_privacy(fingerprint, PrivacyLevel.FULL)
        assert result == fingerprint

    def test_full_is_copy(self):
        """Test FULL returns a new dict (copy)."""
        fingerprint = {"id": "test", "data": {"nested": "value"}}
        result = apply_privacy(fingerprint, PrivacyLevel.FULL)
        assert result is not fingerprint
        assert result == fingerprint


class TestApplyPrivacySchemaVersion:
    """Test schema version preservation."""

    def test_schema_version_default(self):
        """Test default schema version is 1."""
        fingerprint = {"fingerprint_id": "id"}
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert result["schema_version"] == 1

    def test_schema_version_preserved(self):
        """Test schema version is preserved."""
        fingerprint = {
            "fingerprint_id": "id",
            "schema_version": 2,
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert result["schema_version"] == 2


class TestApplyPrivacyLanguage:
    """Test language field."""

    def test_language_preserved(self):
        """Test language field is preserved."""
        fingerprint = {
            "fingerprint_id": "id",
            "language": "python",
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert result["language"] == "python"

    def test_language_missing(self):
        """Test handling of missing language."""
        fingerprint = {"fingerprint_id": "id"}
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        # Result is valid dict
        assert isinstance(result, dict)
        assert "fingerprint_id" in result


class TestApplyPrivacyEdgeCases:
    """Test edge cases."""

    def test_deeply_nested_segments(self):
        """Test deeply nested segment structures."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [
                {
                    "type": "code",
                    "nested": {"deep": {"structure": "value"}},
                }
            ],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert isinstance(result, dict)

    def test_large_segments_list(self):
        """Test with large number of segments."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 10000,
            "segments": [{"type": f"type{i % 10}"} for i in range(1000)],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in result
        assert len(result["segment_type_distribution"]) == 10

    def test_unicode_in_fingerprint(self):
        """Test with unicode in fingerprint."""
        fingerprint = {
            "fingerprint_id": "日本語",
            "language": "ja",
            "segments": [{"type": "テキスト"}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert result["fingerprint_id"] == "日本語"

    def test_handles_dict_with_extra_fields(self):
        """Test handling of unexpected fields."""
        fingerprint = {
            "fingerprint_id": "id",
            "custom_field": "custom",
            "segments": [],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)


class TestApplyPrivacyConsistency:
    """Test consistency across multiple calls."""

    def test_apply_privacy_twice_valid(self):
        """Test that applying privacy twice returns valid dict."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [{"type": "text"}],
        }
        result1 = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        result2 = apply_privacy(result1, PrivacyLevel.STANDARD)
        assert isinstance(result2, dict)

    def test_same_input_same_output(self):
        """Test deterministic behavior."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [{"type": "code"}],
        }
        for _ in range(5):
            result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
            assert result["segment_type_distribution"]["code"] == 1


class TestApplyPrivacyMultipleLevels:
    """Test behavior across different privacy levels."""

    def test_level_progression(self):
        """Test privacy levels from least to most protective."""
        fingerprint = {
            "fingerprint_id": "id",
            "total_tokens": 100,
            "segments": [
                {"type": "text", "hash": "abc"},
                {"type": "code", "hash": "def"},
            ],
        }
        
        minimal = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        standard = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        full = apply_privacy(fingerprint, PrivacyLevel.FULL)
        
        # All should be valid dicts
        assert isinstance(minimal, dict)
        assert isinstance(standard, dict)
        assert isinstance(full, dict)
        
        # FULL should be most detailed
        assert full == fingerprint

    def test_all_levels_preserve_id(self):
        """Test that all levels preserve fingerprint_id."""
        fingerprint = {
            "fingerprint_id": "important_id",
            "total_tokens": 50,
            "segments": [{"type": "text"}],
        }
        
        for level in [PrivacyLevel.MINIMAL, PrivacyLevel.STANDARD, PrivacyLevel.FULL]:
            result = apply_privacy(fingerprint, level)
            assert result["fingerprint_id"] == "important_id"


class TestApplyPrivacyRealWorldScenarios:
    """Test realistic fingerprint scenarios."""

    def test_code_fingerprint(self):
        """Test typical code fingerprint."""
        fingerprint = {
            "fingerprint_id": "code_001",
            "schema_version": 1,
            "total_tokens": 500,
            "segment_count": 3,
            "language": "python",
            "segments": [
                {"type": "import", "token_count": 10, "hash": "h1"},
                {"type": "function", "token_count": 200, "hash": "h2"},
                {"type": "test", "token_count": 290, "hash": "h3"},
            ],
        }
        
        for level in [PrivacyLevel.MINIMAL, PrivacyLevel.STANDARD, PrivacyLevel.FULL]:
            result = apply_privacy(fingerprint, level)
            assert result["total_tokens"] == 500
            assert result["language"] == "python"

    def test_document_fingerprint(self):
        """Test typical document fingerprint."""
        fingerprint = {
            "fingerprint_id": "doc_001",
            "total_tokens": 1000,
            "segment_count": 5,
            "language": "english",
            "segments": [
                {"type": "heading"},
                {"type": "paragraph"},
                {"type": "paragraph"},
                {"type": "code_block"},
                {"type": "paragraph"},
            ],
        }
        
        standard = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert standard["segment_type_distribution"]["paragraph"] == 3
        assert standard["segment_type_distribution"]["code_block"] == 1
