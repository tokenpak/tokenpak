"""
Tests for tokenpak.validation.frontmatter module.

Coverage targets:
- FrontmatterDiagnostics dataclass
- parse_frontmatter() function
- YAML parsing edge cases
- Duplicate key detection
- assigned_to normalization
- Strict vs lenient modes
"""

import pytest

from tokenpak.validation.frontmatter import (
    FrontmatterDiagnostics,
    parse_frontmatter,
)


# ---------------------------------------------------------------------------
# FrontmatterDiagnostics tests
# ---------------------------------------------------------------------------


class TestFrontmatterDiagnostics:
    """Tests for FrontmatterDiagnostics dataclass."""

    def test_default_mode_is_lenient(self):
        """Default mode is 'lenient'."""
        diag = FrontmatterDiagnostics()
        assert diag.mode == "lenient"

    def test_default_lists_are_empty(self):
        """Default lists are empty."""
        diag = FrontmatterDiagnostics()
        assert diag.duplicate_keys == []
        assert diag.warnings == []
        assert diag.errors == []
        assert diag.normalized_fields == []

    def test_has_issues_false_when_clean(self):
        """has_issues is False when no problems."""
        diag = FrontmatterDiagnostics()
        assert diag.has_issues is False

    def test_has_issues_true_with_duplicates(self):
        """has_issues is True when duplicate_keys present."""
        diag = FrontmatterDiagnostics()
        diag.duplicate_keys.append("key1")
        assert diag.has_issues is True

    def test_has_issues_true_with_warnings(self):
        """has_issues is True when warnings present."""
        diag = FrontmatterDiagnostics()
        diag.warnings.append("some warning")
        assert diag.has_issues is True

    def test_has_issues_true_with_errors(self):
        """has_issues is True when errors present."""
        diag = FrontmatterDiagnostics()
        diag.errors.append("some error")
        assert diag.has_issues is True

    def test_to_dict(self):
        """to_dict returns all fields."""
        diag = FrontmatterDiagnostics(mode="strict")
        diag.duplicate_keys = ["key1"]
        diag.warnings = ["warn1"]
        diag.errors = ["err1"]
        diag.normalized_fields = ["field1"]
        
        d = diag.to_dict()
        assert d["mode"] == "strict"
        assert d["duplicate_keys"] == ["key1"]
        assert d["warnings"] == ["warn1"]
        assert d["errors"] == ["err1"]
        assert d["normalized_fields"] == ["field1"]


# ---------------------------------------------------------------------------
# parse_frontmatter() basic tests
# ---------------------------------------------------------------------------


class TestParseFrontmatterBasic:
    """Tests for basic parse_frontmatter functionality."""

    def test_simple_yaml_parsing(self):
        """Simple YAML parses correctly."""
        yaml_block = "title: Test\nstatus: active\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["title"] == "Test"
        assert data["status"] == "active"
        assert diag.has_issues is False

    def test_empty_yaml_returns_empty_dict(self):
        """Empty YAML returns empty dict."""
        data, diag = parse_frontmatter("")
        assert data == {}

    def test_keys_are_sorted(self):
        """Output keys are sorted alphabetically."""
        yaml_block = "zebra: 1\nalpha: 2\nmiddle: 3\n"
        data, diag = parse_frontmatter(yaml_block)
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_lenient_mode_default(self):
        """Default mode is lenient."""
        _, diag = parse_frontmatter("key: value")
        assert diag.mode == "lenient"

    def test_strict_mode_set(self):
        """Strict mode is recorded in diagnostics."""
        _, diag = parse_frontmatter("key: value", strict=True)
        assert diag.mode == "strict"


# ---------------------------------------------------------------------------
# Malformed YAML tests
# ---------------------------------------------------------------------------


class TestParseFrontmatterMalformed:
    """Tests for malformed YAML handling."""

    def test_malformed_yaml_lenient_returns_empty(self):
        """Malformed YAML in lenient mode returns empty dict."""
        yaml_block = "invalid: yaml: content: [["
        data, diag = parse_frontmatter(yaml_block, strict=False)
        assert data == {}
        assert len(diag.errors) > 0
        assert "Malformed YAML" in diag.errors[0]

    def test_malformed_yaml_strict_raises(self):
        """Malformed YAML in strict mode raises ValueError."""
        yaml_block = "invalid: yaml: content: [["
        with pytest.raises(ValueError, match="Malformed YAML"):
            parse_frontmatter(yaml_block, strict=True)

    def test_non_mapping_yaml_lenient(self):
        """Non-mapping YAML (e.g., list) in lenient mode returns empty."""
        yaml_block = "- item1\n- item2\n"
        data, diag = parse_frontmatter(yaml_block, strict=False)
        assert data == {}
        assert len(diag.errors) > 0
        assert "mapping" in diag.errors[0].lower()

    def test_non_mapping_yaml_strict_raises(self):
        """Non-mapping YAML in strict mode raises ValueError."""
        yaml_block = "- item1\n- item2\n"
        with pytest.raises(ValueError, match="mapping"):
            parse_frontmatter(yaml_block, strict=True)


# ---------------------------------------------------------------------------
# Duplicate key tests
# ---------------------------------------------------------------------------


class TestParseFrontmatterDuplicateKeys:
    """Tests for duplicate key detection."""

    def test_duplicate_keys_detected(self):
        """Duplicate keys are detected and reported."""
        yaml_block = "key: value1\nkey: value2\n"
        data, diag = parse_frontmatter(yaml_block, strict=False)
        assert "key" in diag.duplicate_keys
        assert len(diag.warnings) > 0
        # Last value wins in YAML
        assert data["key"] == "value2"

    def test_duplicate_keys_strict_raises(self):
        """Duplicate keys in strict mode raise ValueError."""
        yaml_block = "key: value1\nkey: value2\n"
        with pytest.raises(ValueError, match="Duplicate"):
            parse_frontmatter(yaml_block, strict=True)

    def test_multiple_duplicate_keys(self):
        """Multiple different duplicate keys are all detected."""
        yaml_block = "a: 1\na: 2\nb: 3\nb: 4\n"
        data, diag = parse_frontmatter(yaml_block, strict=False)
        assert "a" in diag.duplicate_keys
        assert "b" in diag.duplicate_keys


# ---------------------------------------------------------------------------
# assigned_to normalization tests
# ---------------------------------------------------------------------------


class TestAssignedToNormalization:
    """Tests for assigned_to field normalization."""

    def test_assigned_to_list_normalized(self):
        """assigned_to list is normalized to list of strings."""
        yaml_block = "assigned_to:\n  - Sue\n  - Trix\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["assigned_to"] == ["Sue", "Trix"]
        assert "assigned_to" in diag.normalized_fields

    def test_assigned_to_comma_separated(self):
        """assigned_to comma-separated string is split."""
        yaml_block = "assigned_to: Sue, Trix, Cali\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["assigned_to"] == ["Sue", "Trix", "Cali"]
        assert "assigned_to" in diag.normalized_fields

    def test_assigned_to_single_string_not_normalized(self):
        """Single string without comma is not converted to list (unless duplicate key)."""
        yaml_block = "assigned_to: Sue\n"
        data, diag = parse_frontmatter(yaml_block)
        # Without comma and no duplicate, should stay as-is
        assert data["assigned_to"] == "Sue"

    def test_assigned_to_whitespace_trimmed(self):
        """Whitespace is trimmed from assigned_to values."""
        yaml_block = "assigned_to:  Sue  ,  Trix  \n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["assigned_to"] == ["Sue", "Trix"]

    def test_assigned_to_empty_values_filtered(self):
        """Empty values in assigned_to are filtered out."""
        yaml_block = "assigned_to: Sue, , , Trix\n"
        data, diag = parse_frontmatter(yaml_block)
        assert "" not in data["assigned_to"]
        assert data["assigned_to"] == ["Sue", "Trix"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestParseFrontmatterEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_null_yaml_value(self):
        """Null YAML value is handled."""
        yaml_block = "key: null\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["key"] is None

    def test_boolean_values(self):
        """Boolean values are parsed correctly."""
        yaml_block = "enabled: true\ndisabled: false\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["enabled"] is True
        assert data["disabled"] is False

    def test_numeric_values(self):
        """Numeric values are parsed correctly."""
        yaml_block = "count: 42\nprice: 9.99\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["count"] == 42
        assert data["price"] == 9.99

    def test_nested_structures(self):
        """Nested dictionaries are parsed."""
        yaml_block = "config:\n  timeout: 30\n  retries: 3\n"
        data, diag = parse_frontmatter(yaml_block)
        assert data["config"]["timeout"] == 30
        assert data["config"]["retries"] == 3

    def test_multiline_string(self):
        """Multiline strings are parsed."""
        yaml_block = "description: |\n  Line 1\n  Line 2\n"
        data, diag = parse_frontmatter(yaml_block)
        assert "Line 1" in data["description"]
        assert "Line 2" in data["description"]
