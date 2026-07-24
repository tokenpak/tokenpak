# SPDX-License-Identifier: Apache-2.0
"""Unit tests for TokenPak validator.py"""

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.core.validator import (
    INVALID_PACK_BAD_BLOCK_TYPE,
    INVALID_PACK_BAD_VERSION,
    INVALID_PACK_DUPLICATE_BLOCK_IDS,
    INVALID_PACK_MISSING_HEADER,
    VALID_PACK_MINIMAL,
    TokenPakValidator,
    ValidationIssue,
    ValidationResult,
)


class TestValidationIssue:
    """Tests for ValidationIssue class."""

    def test_issue_creation_error(self):
        issue = ValidationIssue("error", "header.version", "Invalid version")
        assert issue.level == "error"
        assert issue.field == "header.version"
        assert issue.message == "Invalid version"

    def test_issue_creation_warning(self):
        issue = ValidationIssue("warning", "metadata.tags", "Duplicate tags")
        assert issue.level == "warning"

    def test_issue_creation_info(self):
        issue = ValidationIssue("info", "metadata.target", "Not specified")
        assert issue.level == "info"

    def test_issue_str_error(self):
        issue = ValidationIssue("error", "header.version", "Invalid version")
        str_repr = str(issue)
        assert "✗" in str_repr
        assert "header.version" in str_repr
        assert "Invalid version" in str_repr

    def test_issue_str_warning(self):
        issue = ValidationIssue("warning", "metadata.tags", "Duplicate tags")
        str_repr = str(issue)
        assert "⚠" in str_repr

    def test_issue_str_info(self):
        issue = ValidationIssue("info", "metadata.target", "Not specified")
        str_repr = str(issue)
        assert "ℹ" in str_repr

    def test_issue_to_dict(self):
        issue = ValidationIssue("error", "header.version", "Invalid version")
        d = issue.to_dict()
        assert d == {"level": "error", "field": "header.version", "message": "Invalid version"}


class TestValidationResult:
    """Tests for ValidationResult class."""

    def test_result_creation(self):
        result = ValidationResult()
        assert result.issues == []
        assert result.valid

    def test_result_add_error(self):
        result = ValidationResult()
        result.error("header", "Missing header")
        assert len(result.issues) == 1
        assert result.issues[0].level == "error"
        assert not result.valid

    def test_result_add_warning(self):
        result = ValidationResult()
        result.warning("metadata.tags", "Duplicate tags")
        assert len(result.issues) == 1
        assert result.issues[0].level == "warning"
        assert result.valid  # Warnings don't make it invalid

    def test_result_add_info(self):
        result = ValidationResult()
        result.info("metadata", "Info message")
        assert len(result.issues) == 1
        assert result.issues[0].level == "info"
        assert result.valid

    def test_result_valid_property_no_errors(self):
        result = ValidationResult()
        result.warning("field", "msg")
        result.info("field", "msg")
        assert result.valid

    def test_result_valid_property_with_errors(self):
        result = ValidationResult()
        result.error("field", "msg")
        result.warning("field", "msg")
        assert not result.valid

    def test_result_errors_property(self):
        result = ValidationResult()
        result.error("field1", "error1")
        result.warning("field2", "warning")
        result.error("field3", "error2")
        errors = result.errors
        assert len(errors) == 2
        assert all(i.level == "error" for i in errors)

    def test_result_warnings_property(self):
        result = ValidationResult()
        result.error("field1", "error")
        result.warning("field2", "warning1")
        result.warning("field3", "warning2")
        warnings = result.warnings
        assert len(warnings) == 2
        assert all(i.level == "warning" for i in warnings)

    def test_result_summary_valid(self):
        result = ValidationResult()
        summary = result.summary()
        assert "✓ VALID" in summary
        assert "0 error(s)" in summary
        assert "0 warning(s)" in summary

    def test_result_summary_invalid(self):
        result = ValidationResult()
        result.error("field1", "error1")
        result.warning("field2", "warning1")
        summary = result.summary()
        assert "✗ INVALID" in summary
        assert "1 error(s)" in summary
        assert "1 warning(s)" in summary

    def test_result_to_dict(self):
        result = ValidationResult()
        result.error("field1", "error msg")
        result.warning("field2", "warning msg")
        d = result.to_dict()
        assert d["valid"] is False
        assert d["errors"] == 1
        assert d["warnings"] == 1
        assert len(d["issues"]) == 2


class TestTokenPakValidator:
    """Tests for TokenPakValidator class."""

    @pytest.fixture
    def validator(self):
        return TokenPakValidator()

    # ── Header validation ────

    def test_validate_header_present(self, validator):
        pack = VALID_PACK_MINIMAL
        result = validator.validate(pack)
        assert result.valid

    def test_validate_header_missing(self, validator):
        pack = INVALID_PACK_MISSING_HEADER
        result = validator.validate(pack)
        assert not result.valid
        assert any("header" in i.field for i in result.errors)

    def test_validate_header_not_dict(self, validator):
        pack = {
            "header": "not a dict",
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("header" in i.field and "Must be an object" in i.message for i in result.errors)

    def test_validate_version_missing(self, validator):
        pack = {
            "header": {"id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("version" in i.field for i in result.errors)

    def test_validate_version_invalid_format(self, validator):
        pack = {
            "header": {"version": "invalid", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "version" in i.field and "Invalid version format" in i.message for i in result.errors
        )

    def test_validate_version_unsupported_major(self, validator):
        pack = INVALID_PACK_BAD_VERSION
        result = validator.validate(pack)
        assert not result.valid
        assert any("version" in i.field and "major version" in i.message for i in result.errors)

    def test_validate_version_unknown_minor(self, validator):
        pack = {
            "header": {"version": "1.5", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        # Unknown minor version is a warning, not an error
        assert any("version" in i.field and i.level == "warning" for i in result.issues)

    def test_validate_id_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("header.id" in i.field for i in result.errors)

    def test_validate_id_too_short(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "ab", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("header.id" in i.field for i in result.errors)

    def test_validate_created_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("created" in i.field for i in result.errors)

    def test_validate_created_invalid_iso8601(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "invalid-date"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("created" in i.field and "ISO 8601" in i.message for i in result.errors)

    # ── Metadata validation ────

    def test_validate_metadata_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "metadata" in i.field and "Missing required section" in i.message for i in result.errors
        )

    def test_validate_metadata_not_dict(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": ["not", "a", "dict"],
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "metadata" in i.field and "Must be an object" in i.message for i in result.errors
        )

    def test_validate_task_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("metadata.task" in i.field for i in result.errors)

    def test_validate_source_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("metadata.source" in i.field for i in result.errors)

    def test_validate_tags_duplicate(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test", "tags": ["tag1", "tag2", "tag1"]},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert any("metadata.tags" in i.field and i.level == "warning" for i in result.issues)

    def test_validate_tags_not_list(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test", "tags": "not a list"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "metadata.tags" in i.field and "Must be an array" in i.message for i in result.errors
        )

    def test_validate_expires_past(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test", "expires": "2020-01-01T00:00:00Z"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        # Expires in past is a warning, not error
        assert any("metadata.expires" in i.field and i.level == "warning" for i in result.issues)

    # ── Blocks validation ────

    def test_validate_blocks_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "blocks" in i.field and "Missing required section" in i.message for i in result.errors
        )

    def test_validate_blocks_not_array(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": {"type": "knowledge", "id": "ctx", "content": "test"},
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks" in i.field and "Must be an array" in i.message for i in result.errors)

    def test_validate_blocks_empty(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks" in i.field and "At least one block" in i.message for i in result.errors)

    def test_validate_block_not_dict(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": ["not a dict"],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "blocks[0]" in i.field and "must be an object" in i.message for i in result.errors
        )

    def test_validate_block_type_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].type" in i.field for i in result.errors)

    def test_validate_block_type_invalid(self, validator):
        pack = INVALID_PACK_BAD_BLOCK_TYPE
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "blocks[0].type" in i.field and "Unknown block type" in i.message for i in result.errors
        )

    def test_validate_block_id_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].id" in i.field for i in result.errors)

    def test_validate_block_id_invalid_chars(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "inv@lid", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].id" in i.field for i in result.errors)

    def test_validate_block_id_duplicate(self, validator):
        pack = INVALID_PACK_DUPLICATE_BLOCK_IDS
        result = validator.validate(pack)
        assert not result.valid
        assert any("Duplicate block id" in i.message for i in result.errors)

    def test_validate_block_content_missing(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].content" in i.field for i in result.errors)

    def test_validate_block_content_not_string(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": 123}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any(
            "blocks[0].content" in i.field and "Must be a string" in i.message
            for i in result.errors
        )

    def test_validate_block_priority_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test", "priority": "urgent"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].priority" in i.field for i in result.errors)

    def test_validate_block_quality_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test", "quality": 1.5}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].quality" in i.field for i in result.errors)

    def test_validate_block_tokens_negative(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test", "tokens": -5}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("blocks[0].tokens" in i.field for i in result.errors)

    # ── Multiple blocks ────

    def test_validate_multiple_blocks_valid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [
                {"type": "instructions", "id": "instr", "content": "Do this"},
                {"type": "knowledge", "id": "kn", "content": "Known fact"},
                {"type": "code", "id": "cd", "content": "print('hello')"},
            ],
        }
        result = validator.validate(pack)
        assert result.valid

    # ── File validation ────

    def test_validate_file_not_found(self, validator):
        result = validator.validate_file("/nonexistent/file.json")
        assert not result.valid
        assert any("File not found" in i.message for i in result.errors)

    def test_validate_file_invalid_json(self, validator):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json")
            temp_path = f.name
        try:
            result = validator.validate_file(temp_path)
            assert not result.valid
            assert any("Invalid JSON" in i.message for i in result.errors)
        finally:
            Path(temp_path).unlink()

    def test_validate_file_valid(self, validator):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(VALID_PACK_MINIMAL, f)
            temp_path = f.name
        try:
            result = validator.validate_file(temp_path)
            assert result.valid
        finally:
            Path(temp_path).unlink()

    def test_validate_file_path_object(self, validator):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(VALID_PACK_MINIMAL, f)
            temp_path = Path(f.name)
        try:
            result = validator.validate_file(temp_path)
            assert result.valid
        finally:
            temp_path.unlink()

    # ── Capabilities validation ────

    def test_validate_capabilities_valid(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["capabilities"] = {
            "tools": [
                {"name": "tool1", "description": "Tool 1 description"},
                {"name": "tool2", "description": "Tool 2 description"},
            ],
            "mcp_servers": [
                {"name": "server1", "uri": "stdio:///path/to/server1"},
                {"name": "server2", "uri": "stdio:///path/to/server2"},
            ],
        }
        result = validator.validate(pack)
        assert result.valid

    def test_validate_capabilities_tool_missing_name(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["capabilities"] = {"tools": [{"description": "No name"}]}
        result = validator.validate(pack)
        assert not result.valid
        assert any("capabilities.tools[0].name" in i.field for i in result.errors)

    def test_validate_capabilities_mcp_missing_uri(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["capabilities"] = {"mcp_servers": [{"name": "server1"}]}
        result = validator.validate(pack)
        assert not result.valid
        assert any("capabilities.mcp_servers[0].uri" in i.field for i in result.errors)

    # ── Constraints validation ────

    def test_validate_constraints_max_cost_invalid(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["constraints"] = {"guardrails": {"max_cost_usd": "not a number"}}
        result = validator.validate(pack)
        assert not result.valid
        assert any("max_cost_usd" in i.field for i in result.errors)

    def test_validate_constraints_timeout_invalid(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["constraints"] = {"guardrails": {"timeout_seconds": 0}}
        result = validator.validate(pack)
        assert not result.valid
        assert any("timeout_seconds" in i.field for i in result.errors)

    # ── State validation ────

    def test_validate_state_valid_status(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["state"] = {"status": "in_progress", "step_index": 2}
        result = validator.validate(pack)
        assert result.valid

    def test_validate_state_invalid_status(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["state"] = {"status": "invalid_status"}
        result = validator.validate(pack)
        assert not result.valid
        assert any("state.status" in i.field for i in result.errors)

    def test_validate_state_invalid_step_index(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["state"] = {"step_index": -1}
        result = validator.validate(pack)
        assert not result.valid
        assert any("state.step_index" in i.field for i in result.errors)

    # ── Provenance validation ────

    def test_validate_provenance_valid_trust_level(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["provenance"] = {"trust_level": "verified"}
        result = validator.validate(pack)
        assert result.valid

    def test_validate_provenance_invalid_trust_level(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["provenance"] = {"trust_level": "suspicious"}
        result = validator.validate(pack)
        assert not result.valid
        assert any("trust_level" in i.field for i in result.errors)

    def test_validate_provenance_transforms_unknown_type(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["provenance"] = {"transforms": [{"type": "unknown_transform"}]}
        result = validator.validate(pack)
        # Unknown transform type is a warning, not an error
        assert any(
            "provenance.transforms[0].type" in i.field and i.level == "warning"
            for i in result.issues
        )

    # ── Policies validation ────

    def test_validate_policies_compaction_valid(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["policies"] = {"compaction": {"mode": "balanced", "max_tokens": 1000}}
        result = validator.validate(pack)
        assert result.valid

    def test_validate_policies_compaction_invalid_mode(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["policies"] = {"compaction": {"mode": "invalid_mode"}}
        result = validator.validate(pack)
        assert not result.valid
        assert any("policies.compaction.mode" in i.field for i in result.errors)

    def test_validate_policies_budget_per_block_exceeds_total(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["policies"] = {"budget": {"total": 100, "per_block_max": 200}}
        result = validator.validate(pack)
        assert any("per_block_max" in i.field and i.level == "warning" for i in result.issues)

    # ── Embeddings validation ────

    def test_validate_embeddings_valid(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["embeddings"] = {"block_vectors": {"ctx": [0.1, 0.2, 0.3]}}
        result = validator.validate(pack)
        assert result.valid

    def test_validate_embeddings_unknown_block_reference(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["embeddings"] = {"block_vectors": {"unknown_id": [0.1, 0.2, 0.3]}}
        result = validator.validate(pack)
        assert any("unknown_id" in i.field and i.level == "warning" for i in result.issues)

    # ── Verbose mode ────

    def test_validate_verbose_missing_target(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        result = validator.validate(pack, verbose=True)
        assert any("metadata.target" in i.field and i.level == "info" for i in result.issues)

    def test_validate_verbose_missing_tags(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        result = validator.validate(pack, verbose=True)
        assert any("metadata.tags" in i.field and i.level == "info" for i in result.issues)

    def test_validate_verbose_no_instructions_block(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        result = validator.validate(pack, verbose=True)
        assert any("blocks" in i.field and i.level == "info" for i in result.issues)

    # ── Edge cases ────

    def test_validate_block_quality_zero(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["quality"] = 0.0
        result = validator.validate(pack)
        assert result.valid

    def test_validate_block_quality_one(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["quality"] = 1.0
        result = validator.validate(pack)
        assert result.valid

    def test_validate_block_tokens_zero(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["tokens"] = 0
        result = validator.validate(pack)
        assert result.valid

    def test_validate_block_id_with_underscore(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["id"] = "block_id_123"
        result = validator.validate(pack)
        assert result.valid

    def test_validate_block_id_with_dash(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["id"] = "block-id-123"
        result = validator.validate(pack)
        assert result.valid

    def test_validate_block_id_with_dot(self, validator):
        pack = VALID_PACK_MINIMAL.copy()
        pack["blocks"][0]["id"] = "block.id.123"
        result = validator.validate(pack)
        assert result.valid

    def test_validate_all_block_types(self, validator):
        """Test that all supported block types are valid."""
        block_types = [
            "instructions",
            "code",
            "knowledge",
            "memory",
            "conversation",
            "evidence",
            "system",
        ]
        for block_type in block_types:
            pack = {
                "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
                "metadata": {"task": "test", "source": "agent:test"},
                "blocks": [{"type": block_type, "id": "ctx", "content": "test"}],
            }
            result = validator.validate(pack)
            assert result.valid, f"Block type '{block_type}' should be valid"

    def test_validate_all_priority_values(self, validator):
        """Test that all priority values are valid."""
        priority_values = ["critical", "high", "medium", "low", "internal"]
        for priority in priority_values:
            pack = VALID_PACK_MINIMAL.copy()
            pack["blocks"][0]["priority"] = priority
            result = validator.validate(pack)
            assert result.valid, f"Priority '{priority}' should be valid"

    def test_validate_iso8601_z_suffix(self, validator):
        """Test ISO 8601 timestamp with Z suffix."""
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T12:34:56Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert result.valid

    def test_validate_iso8601_plus_offset(self, validator):
        """Test ISO 8601 timestamp with +HH:MM offset."""
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T12:34:56+05:30"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert result.valid

    def test_validate_empty_metadata_task_string(self, validator):
        """Empty task string should fail."""
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "   ", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("metadata.task" in i.field for i in result.errors)

    def test_validate_empty_content_string(self, validator):
        """Empty content string should be allowed."""
        pack = {
            "header": {"version": "1.0", "id": "pak_test", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:test"},
            "blocks": [{"type": "knowledge", "id": "ctx", "content": ""}],
        }
        result = validator.validate(pack)
        assert result.valid
