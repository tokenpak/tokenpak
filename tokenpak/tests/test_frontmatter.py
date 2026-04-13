"""Unit tests for validation/frontmatter.py"""
from __future__ import annotations

import pytest

from tokenpak.validation.frontmatter import FrontmatterDiagnostics, parse_frontmatter


# ---------------------------------------------------------------------------
# FrontmatterDiagnostics
# ---------------------------------------------------------------------------


class TestFrontmatterDiagnostics:
    def test_has_issues_clean(self):
        d = FrontmatterDiagnostics()
        assert not d.has_issues

    def test_has_issues_with_duplicate_keys(self):
        d = FrontmatterDiagnostics(duplicate_keys=["title"])
        assert d.has_issues

    def test_has_issues_with_warnings(self):
        d = FrontmatterDiagnostics(warnings=["something is off"])
        assert d.has_issues

    def test_has_issues_with_errors(self):
        d = FrontmatterDiagnostics(errors=["bad yaml"])
        assert d.has_issues

    def test_has_issues_false_with_only_normalized(self):
        # normalized_fields alone does not count as an issue
        d = FrontmatterDiagnostics(normalized_fields=["assigned_to"])
        assert not d.has_issues

    def test_default_mode_lenient(self):
        d = FrontmatterDiagnostics()
        assert d.mode == "lenient"

    def test_to_dict_clean(self):
        d = FrontmatterDiagnostics()
        result = d.to_dict()
        assert result["mode"] == "lenient"
        assert result["duplicate_keys"] == []
        assert result["warnings"] == []
        assert result["errors"] == []
        assert result["normalized_fields"] == []

    def test_to_dict_with_data(self):
        d = FrontmatterDiagnostics(
            mode="strict",
            duplicate_keys=["title"],
            warnings=["dup key"],
            errors=["bad"],
            normalized_fields=["assigned_to"],
        )
        result = d.to_dict()
        assert result["mode"] == "strict"
        assert "title" in result["duplicate_keys"]
        assert "dup key" in result["warnings"]
        assert "bad" in result["errors"]
        assert "assigned_to" in result["normalized_fields"]


# ---------------------------------------------------------------------------
# parse_frontmatter — valid YAML
# ---------------------------------------------------------------------------


class TestParseFrontmatterValid:
    def test_simple_key_value(self):
        yaml = "title: My Task\nstatus: open"
        data, diag = parse_frontmatter(yaml)
        assert data["title"] == "My Task"
        assert data["status"] == "open"
        assert not diag.has_issues

    def test_empty_string_returns_empty_dict(self):
        data, diag = parse_frontmatter("")
        assert data == {}
        assert not diag.errors

    def test_whitespace_only_returns_empty_dict(self):
        data, diag = parse_frontmatter("   \n  ")
        assert data == {}

    def test_integer_value(self):
        yaml = "priority: 5\nstatus: open"
        data, diag = parse_frontmatter(yaml)
        assert data["priority"] == 5

    def test_list_value(self):
        yaml = "tags:\n  - alpha\n  - beta"
        data, diag = parse_frontmatter(yaml)
        assert data["tags"] == ["alpha", "beta"]

    def test_nested_dict(self):
        yaml = "meta:\n  owner: Trix\n  version: 1"
        data, diag = parse_frontmatter(yaml)
        assert data["meta"]["owner"] == "Trix"

    def test_strict_mode_flag_in_diagnostics(self):
        _, diag = parse_frontmatter("key: value", strict=False)
        assert diag.mode == "lenient"

    def test_strict_mode_flag_strict(self):
        _, diag = parse_frontmatter("key: value", strict=True)
        assert diag.mode == "strict"


# ---------------------------------------------------------------------------
# parse_frontmatter — canonical ordering
# ---------------------------------------------------------------------------


class TestParseFrontmatterCanonicalOrdering:
    def test_keys_sorted_alphabetically(self):
        yaml = "zebra: z\napple: a\nmango: m"
        data, _ = parse_frontmatter(yaml)
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_original_values_preserved_after_sort(self):
        yaml = "z_field: last\na_field: first"
        data, _ = parse_frontmatter(yaml)
        assert data["a_field"] == "first"
        assert data["z_field"] == "last"


# ---------------------------------------------------------------------------
# parse_frontmatter — malformed YAML
# ---------------------------------------------------------------------------


class TestParseFrontmatterMalformed:
    def test_malformed_yaml_lenient_returns_empty_dict(self):
        bad_yaml = "key: [\nbad yaml"
        data, diag = parse_frontmatter(bad_yaml, strict=False)
        assert data == {}
        assert diag.errors

    def test_malformed_yaml_error_message(self):
        bad_yaml = "key: [\nbad yaml"
        _, diag = parse_frontmatter(bad_yaml, strict=False)
        assert any("Malformed YAML" in e for e in diag.errors)

    def test_malformed_yaml_strict_raises_value_error(self):
        bad_yaml = "key: [\nbad yaml"
        with pytest.raises(ValueError, match="Malformed YAML"):
            parse_frontmatter(bad_yaml, strict=True)

    def test_non_dict_yaml_lenient_returns_empty_dict(self):
        yaml = "- item1\n- item2"
        data, diag = parse_frontmatter(yaml, strict=False)
        assert data == {}
        assert diag.errors

    def test_non_dict_yaml_strict_raises(self):
        yaml = "- item1\n- item2"
        with pytest.raises(ValueError, match="mapping"):
            parse_frontmatter(yaml, strict=True)

    def test_scalar_yaml_lenient_returns_empty_dict(self):
        data, diag = parse_frontmatter("just a string", strict=False)
        assert data == {}
        assert diag.errors


# ---------------------------------------------------------------------------
# parse_frontmatter — duplicate keys
# ---------------------------------------------------------------------------


class TestParseFrontmatterDuplicateKeys:
    def test_duplicate_key_detected(self):
        yaml = "title: First\ntitle: Second"
        data, diag = parse_frontmatter(yaml)
        assert "title" in diag.duplicate_keys

    def test_duplicate_key_produces_warning(self):
        yaml = "title: First\ntitle: Second"
        _, diag = parse_frontmatter(yaml)
        assert diag.warnings

    def test_duplicate_key_last_value_wins(self):
        yaml = "title: First\ntitle: Second"
        data, _ = parse_frontmatter(yaml)
        assert data["title"] == "Second"

    def test_duplicate_key_strict_raises(self):
        yaml = "title: First\ntitle: Second"
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            parse_frontmatter(yaml, strict=True)

    def test_no_duplicate_keys_clean(self):
        yaml = "title: Only\nstatus: open"
        _, diag = parse_frontmatter(yaml)
        assert diag.duplicate_keys == []


# ---------------------------------------------------------------------------
# parse_frontmatter — assigned_to normalization
# ---------------------------------------------------------------------------


class TestParseFrontmatterAssignedTo:
    def test_assigned_to_list_preserved(self):
        yaml = "assigned_to:\n  - Alice\n  - Bob"
        data, diag = parse_frontmatter(yaml)
        assert data["assigned_to"] == ["Alice", "Bob"]
        assert "assigned_to" in diag.normalized_fields

    def test_assigned_to_comma_string_split(self):
        yaml = "assigned_to: Alice, Bob, Carol"
        data, diag = parse_frontmatter(yaml)
        assert data["assigned_to"] == ["Alice", "Bob", "Carol"]
        assert "assigned_to" in diag.normalized_fields

    def test_assigned_to_comma_string_strips_whitespace(self):
        yaml = "assigned_to: Alice ,  Bob  , Carol"
        data, diag = parse_frontmatter(yaml)
        assert data["assigned_to"] == ["Alice", "Bob", "Carol"]

    def test_assigned_to_plain_string_no_normalization(self):
        # A plain string without comma and without duplicate key stays as-is
        yaml = "assigned_to: Alice"
        data, diag = parse_frontmatter(yaml)
        assert data["assigned_to"] == "Alice"
        assert "assigned_to" not in diag.normalized_fields

    def test_assigned_to_list_filters_empty_items(self):
        yaml = "assigned_to:\n  - Alice\n  - ''\n  - Bob"
        data, _ = parse_frontmatter(yaml)
        assert "" not in data["assigned_to"]
        assert "Alice" in data["assigned_to"]
        assert "Bob" in data["assigned_to"]

    def test_assigned_to_absent_no_normalization(self):
        yaml = "title: My Task"
        data, diag = parse_frontmatter(yaml)
        assert "assigned_to" not in data
        assert "assigned_to" not in diag.normalized_fields

    def test_assigned_to_duplicate_key_normalizes_to_list(self):
        # Duplicate key with plain string → normalized to single-element list
        yaml = "assigned_to: Alice\nassigned_to: Bob"
        data, diag = parse_frontmatter(yaml)
        # Last value wins (Bob), duplicate key detected, so it normalizes
        assert isinstance(data["assigned_to"], list)
        assert "assigned_to" in diag.normalized_fields

    def test_assigned_to_comma_string_single_value(self):
        yaml = "assigned_to: Alice,"
        data, _ = parse_frontmatter(yaml)
        # "Alice," has a comma → split → ["Alice"] (trailing empty item filtered)
        assert data["assigned_to"] == ["Alice"]
