"""
Test vectors for TokenPak Protocol v1.0 validation.

Tests cover:
- Valid packs (minimal, full, all examples)
- Invalid packs (missing required fields, bad types, constraint violations)
- Warning conditions (expired TTL, quality hints)
- Round-trip: all example files load and validate
"""

import json
import pytest
from pathlib import Path

from tokenpak.validator import (
    TokenPakValidator,
    VALID_PACK_MINIMAL,
    INVALID_PACK_MISSING_HEADER,
    INVALID_PACK_BAD_VERSION,
    INVALID_PACK_NO_BLOCKS,
    INVALID_PACK_BAD_BLOCK_TYPE,
    INVALID_PACK_DUPLICATE_BLOCK_IDS,
)

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"


@pytest.fixture
def validator():
    return TokenPakValidator()


# ── Valid packs ───────────────────────────────────────────────────────────────

class TestValidPacks:

    def test_minimal_pack_is_valid(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL)
        assert result.valid, f"Expected valid, got errors: {result.errors}"
        assert len(result.errors) == 0

    def test_minimal_pack_has_no_errors(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL)
        assert len(result.errors) == 0

    def test_version_1_0_accepted(self, validator):
        pack = {**VALID_PACK_MINIMAL, "header": {**VALID_PACK_MINIMAL["header"], "version": "1.0"}}
        result = validator.validate(pack)
        assert result.valid

    def test_all_block_types_accepted(self, validator):
        types = ["instructions", "code", "knowledge", "memory", "conversation", "evidence", "system"]
        for btype in types:
            pack = {
                "header": {"version": "1.0", "id": f"pak_{btype}", "created": "2026-03-07T00:00:00Z"},
                "metadata": {"task": "test", "source": "agent:test"},
                "blocks": [{"type": btype, "id": "blk", "content": "content"}]
            }
            result = validator.validate(pack)
            assert result.valid, f"Block type '{btype}' should be valid, got: {result.errors}"

    def test_all_priority_values_accepted(self, validator):
        for priority in ["critical", "high", "medium", "low", "internal"]:
            pack = {
                "header": {"version": "1.0", "id": "pak_pri", "created": "2026-03-07T00:00:00Z"},
                "metadata": {"task": "test", "source": "agent:test"},
                "blocks": [{"type": "knowledge", "id": "blk", "content": "content", "priority": priority}]
            }
            result = validator.validate(pack)
            assert result.valid, f"Priority '{priority}' should be valid"

    def test_all_trust_levels_accepted(self, validator):
        for trust in ["verified", "unverified", "generated"]:
            pack = {**VALID_PACK_MINIMAL, "provenance": {"trust_level": trust}}
            result = validator.validate(pack)
            assert result.valid, f"Trust level '{trust}' should be valid"

    def test_optional_sections_can_be_omitted(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL)
        assert result.valid

    def test_full_pack_with_all_sections_is_valid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_full", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test", "source": "agent:a", "target": "agent:b", "tags": ["x"]},
            "blocks": [
                {"type": "instructions", "id": "sys", "content": "Be helpful.", "priority": "critical"},
                {"type": "knowledge", "id": "ctx", "content": "Context.", "priority": "high", "quality": 0.9},
            ],
            "capabilities": {
                "tools": [{"name": "read", "description": "Read a file", "provider": "local"}],
                "mcp_servers": [{"uri": "mcp://localhost/fs", "name": "fs"}]
            },
            "constraints": {
                "model": {"requires_tools": True},
                "guardrails": {"max_cost_usd": 1.0, "timeout_seconds": 60}
            },
            "state": {"workflow_id": "wf1", "step_index": 0, "resumable": True, "status": "in_progress"},
            "provenance": {
                "source_packs": [],
                "transforms": [{"type": "enrich", "agent": "agent:a"}],
                "trust_level": "verified"
            },
            "policies": {
                "compaction": {"mode": "balanced", "max_tokens": 8000},
                "budget": {"total": 8000, "per_block_max": 2000, "reserve_for_output": 2000}
            }
        }
        result = validator.validate(pack)
        assert result.valid, f"Full pack errors: {[(e.field, e.message) for e in result.errors]}"

    def test_block_id_with_hyphens_and_dots_accepted(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_ids", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "my-block.v2_test", "content": "x"}]
        }
        result = validator.validate(pack)
        assert result.valid

    def test_quality_score_zero_accepted(self, validator):
        pack = {**VALID_PACK_MINIMAL}
        pack["blocks"] = [{"type": "evidence", "id": "b", "content": "x", "quality": 0.0}]
        result = validator.validate(pack)
        assert result.valid

    def test_quality_score_one_accepted(self, validator):
        pack = {**VALID_PACK_MINIMAL}
        pack["blocks"] = [{"type": "evidence", "id": "b", "content": "x", "quality": 1.0}]
        result = validator.validate(pack)
        assert result.valid


# ── Invalid packs ─────────────────────────────────────────────────────────────

class TestInvalidPacks:

    def test_missing_header_is_invalid(self, validator):
        result = validator.validate(INVALID_PACK_MISSING_HEADER)
        assert not result.valid
        assert any("header" in e.field for e in result.errors)

    def test_bad_major_version_is_invalid(self, validator):
        result = validator.validate(INVALID_PACK_BAD_VERSION)
        assert not result.valid
        assert any("version" in e.field for e in result.errors)

    def test_empty_blocks_is_invalid(self, validator):
        result = validator.validate(INVALID_PACK_NO_BLOCKS)
        assert not result.valid
        assert any("blocks" in e.field for e in result.errors)

    def test_unknown_block_type_is_invalid(self, validator):
        result = validator.validate(INVALID_PACK_BAD_BLOCK_TYPE)
        assert not result.valid
        assert any("type" in e.field for e in result.errors)

    def test_duplicate_block_ids_is_invalid(self, validator):
        result = validator.validate(INVALID_PACK_DUPLICATE_BLOCK_IDS)
        assert not result.valid
        assert any("id" in e.field for e in result.errors)

    def test_missing_metadata_is_invalid(self, validator):
        pack = {"header": VALID_PACK_MINIMAL["header"], "blocks": VALID_PACK_MINIMAL["blocks"]}
        result = validator.validate(pack)
        assert not result.valid
        assert any("metadata" in e.field for e in result.errors)

    def test_missing_task_in_metadata_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"source": "agent:x"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("task" in e.field for e in result.errors)

    def test_missing_source_in_metadata_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "test"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid
        assert any("source" in e.field for e in result.errors)

    def test_missing_header_id_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid

    def test_missing_header_created_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid

    def test_block_missing_content_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "b"}]
        }
        result = validator.validate(pack)
        assert not result.valid

    def test_block_missing_id_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid

    def test_invalid_priority_is_invalid(self, validator):
        pack = {**VALID_PACK_MINIMAL}
        pack["blocks"] = [{"type": "knowledge", "id": "b", "content": "x", "priority": "extreme"}]
        result = validator.validate(pack)
        assert not result.valid

    def test_quality_out_of_range_is_invalid(self, validator):
        pack = {**VALID_PACK_MINIMAL}
        pack["blocks"] = [{"type": "knowledge", "id": "b", "content": "x", "quality": 1.5}]
        result = validator.validate(pack)
        assert not result.valid

    def test_invalid_created_timestamp_is_invalid(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "not-a-date"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert not result.valid

    def test_invalid_compaction_mode_is_invalid(self, validator):
        pack = {**VALID_PACK_MINIMAL, "policies": {"compaction": {"mode": "ultra-turbo"}}}
        result = validator.validate(pack)
        assert not result.valid

    def test_unknown_trust_level_is_invalid(self, validator):
        pack = {**VALID_PACK_MINIMAL, "provenance": {"trust_level": "trusted"}}
        result = validator.validate(pack)
        assert not result.valid

    def test_nonexistent_file_returns_error(self, validator):
        result = validator.validate_file("/nonexistent/path/pack.json")
        assert not result.valid
        assert any("not found" in e.message.lower() for e in result.errors)

    def test_invalid_json_file_returns_error(self, validator, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ not valid json }")
        result = validator.validate_file(bad_file)
        assert not result.valid
        assert any("Invalid JSON" in e.message for e in result.errors)


# ── Warning conditions ────────────────────────────────────────────────────────

class TestWarnings:

    def test_past_expires_generates_warning(self, validator):
        pack = {**VALID_PACK_MINIMAL}
        pack["metadata"] = {**pack["metadata"], "expires": "2020-01-01T00:00:00Z"}
        result = validator.validate(pack)
        # Should be valid (warnings don't fail), but have a warning
        assert result.valid
        assert any("past" in w.message.lower() or "expired" in w.message.lower() for w in result.warnings)

    def test_per_block_max_exceeds_total_generates_warning(self, validator):
        pack = {**VALID_PACK_MINIMAL, "policies": {
            "budget": {"total": 1000, "per_block_max": 2000, "reserve_for_output": 0}
        }}
        result = validator.validate(pack)
        assert result.valid
        assert any("per_block_max" in w.field for w in result.warnings)

    def test_unknown_minor_version_generates_warning(self, validator):
        pack = {
            "header": {"version": "1.99", "id": "pak_future", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a"},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert result.valid  # minor version = warn but don't fail
        assert any("version" in w.field for w in result.warnings)

    def test_duplicate_tags_generate_warning(self, validator):
        pack = {
            "header": {"version": "1.0", "id": "pak_x", "created": "2026-03-07T00:00:00Z"},
            "metadata": {"task": "t", "source": "a", "tags": ["foo", "foo"]},
            "blocks": [{"type": "knowledge", "id": "b", "content": "x"}]
        }
        result = validator.validate(pack)
        assert result.valid
        assert any("tags" in w.field.lower() for w in result.warnings)


# ── Verbose / quality hints ───────────────────────────────────────────────────

class TestVerboseHints:

    def test_verbose_flags_missing_target(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL, verbose=True)
        assert result.valid
        assert any("target" in i.field for i in result.issues if i.level == "info")

    def test_verbose_flags_missing_instructions_block(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL, verbose=True)
        assert result.valid
        assert any("instructions" in i.message.lower() for i in result.issues if i.level == "info")

    def test_verbose_does_not_introduce_errors(self, validator):
        result = validator.validate(VALID_PACK_MINIMAL, verbose=True)
        assert result.valid
        assert len(result.errors) == 0


# ── Round-trip: example files ─────────────────────────────────────────────────

class TestExampleFiles:

    @pytest.mark.parametrize("example_file", [
        "minimal.tokenpak.json",
        "full.tokenpak.json",
        "agent_handoff.tokenpak.json",
        "mcp_enabled.tokenpak.json",
        "rag_retrieval.tokenpak.json",
    ])
    def test_example_file_is_valid(self, validator, example_file):
        path = EXAMPLES_DIR / example_file
        if not path.exists():
            pytest.skip(f"Example file not found: {path}")
        result = validator.validate_file(path)
        assert result.valid, (
            f"Example '{example_file}' failed validation:\n"
            + "\n".join(str(e) for e in result.errors)
        )

    @pytest.mark.parametrize("example_file", [
        "minimal.tokenpak.json",
        "full.tokenpak.json",
        "agent_handoff.tokenpak.json",
        "mcp_enabled.tokenpak.json",
        "rag_retrieval.tokenpak.json",
    ])
    def test_example_file_is_valid_json(self, example_file):
        path = EXAMPLES_DIR / example_file
        if not path.exists():
            pytest.skip(f"Example file not found: {path}")
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    @pytest.mark.parametrize("example_file", [
        "minimal.tokenpak.json",
        "full.tokenpak.json",
        "agent_handoff.tokenpak.json",
        "mcp_enabled.tokenpak.json",
        "rag_retrieval.tokenpak.json",
    ])
    def test_example_file_has_required_sections(self, example_file):
        path = EXAMPLES_DIR / example_file
        if not path.exists():
            pytest.skip(f"Example file not found: {path}")
        with open(path) as f:
            data = json.load(f)
        assert "header" in data
        assert "metadata" in data
        assert "blocks" in data
        assert len(data["blocks"]) > 0


# ── Schema file ───────────────────────────────────────────────────────────────

class TestSchemaFile:

    def test_schema_file_exists(self):
        assert (SCHEMAS_DIR / "tokenpak-v1.0.json").exists()

    def test_schema_file_is_valid_json(self):
        with open(SCHEMAS_DIR / "tokenpak-v1.0.json") as f:
            schema = json.load(f)
        assert isinstance(schema, dict)

    def test_schema_has_required_fields(self):
        with open(SCHEMAS_DIR / "tokenpak-v1.0.json") as f:
            schema = json.load(f)
        assert schema.get("$schema")
        assert schema.get("title")
        assert "properties" in schema
        assert "required" in schema
        assert set(schema["required"]) == {"header", "metadata", "blocks"}
