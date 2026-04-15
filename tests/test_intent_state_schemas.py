# SPDX-License-Identifier: MIT
"""Tests for intent-specific state schemas and MultiSchemaStateManager.

Verifies:
  - All 5 schema files exist and are valid JSON
  - IntentStateManager initializes correct default fields per intent
  - MultiSchemaStateManager routes to correct intent sub-manager
  - Wire format is compact and tagged with intent
  - State isolation: setting debug fields doesn't pollute plan state
  - select_state_manager factory returns correct IntentStateManager type
"""


import pytest
pytest.importorskip("tokenpak.infrastructure", reason="module not available in current build")
import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.infrastructure.state_manager import (
    IntentStateManager,
    MultiSchemaStateManager,
    select_state_manager,
)
from tokenpak._internal.state_schemas import (
    INTENT_SCHEMA_MAP,
    SCHEMAS_DIR,
    get_schema_path,
)


# ---------------------------------------------------------------------------
# Schema file existence + validity
# ---------------------------------------------------------------------------

class TestSchemaFiles:
    """All schema JSON files must exist and be valid JSON."""

    EXPECTED_SCHEMAS = [
        "debug_state.json",
        "writing_state.json",
        "planning_state.json",
        "ops_state.json",
        "extraction_state.json",
    ]

    def test_all_schema_files_exist(self):
        for filename in self.EXPECTED_SCHEMAS:
            path = SCHEMAS_DIR / filename
            assert path.exists(), f"Missing schema file: {filename}"

    def test_all_schema_files_are_valid_json(self):
        for filename in self.EXPECTED_SCHEMAS:
            path = SCHEMAS_DIR / filename
            with open(path) as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{filename} must be a JSON object"
            assert "properties" in data, f"{filename} must have 'properties'"

    def test_schema_files_have_required_fields_declared(self):
        """Each schema should declare at least one required field."""
        for filename in self.EXPECTED_SCHEMAS:
            path = SCHEMAS_DIR / filename
            with open(path) as f:
                data = json.load(f)
            assert "required" in data, f"{filename} must declare 'required' fields"
            assert len(data["required"]) >= 1

    def test_debug_state_schema_fields(self):
        path = SCHEMAS_DIR / "debug_state.json"
        with open(path) as f:
            schema = json.load(f)
        props = schema["properties"]
        for field in ("error", "affected_files", "changed_files", "failing_tests", "recent_deploy"):
            assert field in props, f"debug_state.json missing field: {field}"

    def test_writing_state_schema_fields(self):
        path = SCHEMAS_DIR / "writing_state.json"
        with open(path) as f:
            schema = json.load(f)
        props = schema["properties"]
        for field in ("audience", "tone", "cta", "brand_constraints", "source_points"):
            assert field in props, f"writing_state.json missing field: {field}"

    def test_planning_state_schema_fields(self):
        path = SCHEMAS_DIR / "planning_state.json"
        with open(path) as f:
            schema = json.load(f)
        props = schema["properties"]
        for field in ("objective", "constraints", "options", "blockers", "deadline"):
            assert field in props, f"planning_state.json missing field: {field}"

    def test_ops_state_schema_fields(self):
        path = SCHEMAS_DIR / "ops_state.json"
        with open(path) as f:
            schema = json.load(f)
        props = schema["properties"]
        for field in ("service_status", "recent_changes", "health_checks", "env_drift"):
            assert field in props, f"ops_state.json missing field: {field}"

    def test_extraction_state_schema_fields(self):
        path = SCHEMAS_DIR / "extraction_state.json"
        with open(path) as f:
            schema = json.load(f)
        props = schema["properties"]
        for field in ("schema", "source_type", "output_format"):
            assert field in props, f"extraction_state.json missing field: {field}"


# ---------------------------------------------------------------------------
# INTENT_SCHEMA_MAP + get_schema_path
# ---------------------------------------------------------------------------

class TestIntentSchemaMap:
    def test_map_covers_expected_intents(self):
        expected = {"debug", "create", "plan", "execute", "query", "search"}
        assert expected.issubset(set(INTENT_SCHEMA_MAP.keys()))

    def test_get_schema_path_returns_path_for_known_intent(self):
        path = get_schema_path("debug")
        assert path is not None
        assert path.exists()

    def test_get_schema_path_returns_none_for_unknown_intent(self):
        path = get_schema_path("nonexistent_intent_xyz")
        assert path is None


# ---------------------------------------------------------------------------
# IntentStateManager — defaults + field isolation
# ---------------------------------------------------------------------------

class TestIntentStateManager:
    def _make(self, intent: str, tmp_dir: str) -> IntentStateManager:
        return IntentStateManager("test-session", intent, base_dir=tmp_dir)

    def test_debug_manager_initializes_correct_fields(self, tmp_path):
        mgr = self._make("debug", str(tmp_path))
        assert "error" in mgr.state
        assert "affected_files" in mgr.state
        assert "failing_tests" in mgr.state

    def test_plan_manager_initializes_correct_fields(self, tmp_path):
        mgr = self._make("plan", str(tmp_path))
        assert "objective" in mgr.state
        assert "constraints" in mgr.state
        assert "blockers" in mgr.state

    def test_create_manager_initializes_correct_fields(self, tmp_path):
        mgr = self._make("create", str(tmp_path))
        assert "audience" in mgr.state
        assert "tone" in mgr.state
        assert "brand_constraints" in mgr.state

    def test_execute_manager_initializes_correct_fields(self, tmp_path):
        mgr = self._make("execute", str(tmp_path))
        assert "service_status" in mgr.state
        assert "recent_changes" in mgr.state

    def test_query_manager_initializes_correct_fields(self, tmp_path):
        mgr = self._make("query", str(tmp_path))
        assert "schema" in mgr.state
        assert "source_type" in mgr.state
        assert "output_format" in mgr.state

    def test_set_and_get_field(self, tmp_path):
        mgr = self._make("debug", str(tmp_path))
        mgr.set("error", "AttributeError: 'NoneType' object has no attribute 'id'")
        assert mgr.get("error") == "AttributeError: 'NoneType' object has no attribute 'id'"

    def test_update_patch(self, tmp_path):
        mgr = self._make("plan", str(tmp_path))
        mgr.update({"objective": "migrate to Postgres", "deadline": "2026-04-01"})
        assert mgr.state["objective"] == "migrate to Postgres"
        assert mgr.state["deadline"] == "2026-04-01"

    def test_wire_format_is_compact_json(self, tmp_path):
        mgr = self._make("debug", str(tmp_path))
        mgr.set("error", "boom")
        wire = mgr.to_wire_format()
        assert "\n" not in wire  # no whitespace
        data = json.loads(wire)
        assert data["error"] == "boom"

    def test_wire_section_tagged_with_intent(self, tmp_path):
        mgr = self._make("debug", str(tmp_path))
        section = mgr.to_wire_section()
        assert section.startswith("STATE_JSON[debug]:")

    def test_save_and_reload(self, tmp_path):
        mgr = self._make("debug", str(tmp_path))
        mgr.set("error", "saved error")
        mgr.set("recent_deploy", "v1.2.3")
        mgr.save()

        mgr2 = self._make("debug", str(tmp_path))
        assert mgr2.state["error"] == "saved error"
        assert mgr2.state["recent_deploy"] == "v1.2.3"


# ---------------------------------------------------------------------------
# State isolation: intent sub-managers don't share state
# ---------------------------------------------------------------------------

class TestStateIsolation:
    def test_debug_and_plan_states_are_isolated(self, tmp_path):
        ms = MultiSchemaStateManager("iso-sess", base_dir=str(tmp_path))
        ms.for_intent("debug").set("error", "critical failure")
        ms.for_intent("plan").set("objective", "world domination")

        # Debug manager should NOT have "objective"
        assert "objective" not in ms.for_intent("debug").state
        # Plan manager should NOT have "error"
        assert "error" not in ms.for_intent("plan").state

    def test_different_intents_have_different_field_sets(self, tmp_path):
        ms = MultiSchemaStateManager("field-sess", base_dir=str(tmp_path))
        debug_keys = set(ms.for_intent("debug").state.keys())
        plan_keys = set(ms.for_intent("plan").state.keys())
        # They should not be identical
        assert debug_keys != plan_keys


# ---------------------------------------------------------------------------
# MultiSchemaStateManager
# ---------------------------------------------------------------------------

class TestMultiSchemaStateManager:
    def test_for_intent_returns_intent_state_manager(self, tmp_path):
        ms = MultiSchemaStateManager("ms-sess", base_dir=str(tmp_path))
        mgr = ms.for_intent("debug")
        assert isinstance(mgr, IntentStateManager)
        assert mgr.intent == "debug"

    def test_for_intent_returns_same_instance(self, tmp_path):
        ms = MultiSchemaStateManager("ms-sess", base_dir=str(tmp_path))
        mgr1 = ms.for_intent("plan")
        mgr2 = ms.for_intent("plan")
        assert mgr1 is mgr2

    def test_active_intents_tracks_accessed(self, tmp_path):
        ms = MultiSchemaStateManager("track-sess", base_dir=str(tmp_path))
        ms.for_intent("debug")
        ms.for_intent("plan")
        assert set(ms.active_intents()) == {"debug", "plan"}

    def test_build_wire_section_for_intent(self, tmp_path):
        ms = MultiSchemaStateManager("wire-sess", base_dir=str(tmp_path))
        ms.for_intent("debug").set("error", "NullPointer")
        section = ms.build_wire_section("debug")
        assert "STATE_JSON[debug]:" in section
        assert "NullPointer" in section

    def test_save_all_persists_all_managers(self, tmp_path):
        ms = MultiSchemaStateManager("save-sess", base_dir=str(tmp_path))
        ms.for_intent("debug").set("error", "persisted")
        ms.for_intent("plan").set("objective", "also persisted")
        ms.save_all()

        ms2 = MultiSchemaStateManager("save-sess", base_dir=str(tmp_path))
        assert ms2.for_intent("debug").state["error"] == "persisted"
        assert ms2.for_intent("plan").state["objective"] == "also persisted"


# ---------------------------------------------------------------------------
# select_state_manager factory
# ---------------------------------------------------------------------------

class TestSelectStateManager:
    def test_returns_intent_state_manager(self, tmp_path):
        mgr = select_state_manager("factory-sess", "debug", base_dir=str(tmp_path))
        assert isinstance(mgr, IntentStateManager)

    def test_factory_selects_correct_intent(self, tmp_path):
        mgr = select_state_manager("factory-sess", "plan", base_dir=str(tmp_path))
        assert mgr.intent == "plan"
        assert "objective" in mgr.state

    def test_factory_unknown_intent_still_creates_manager(self, tmp_path):
        """Unknown intents should not crash — they just get empty state."""
        mgr = select_state_manager("factory-sess", "unknown_intent", base_dir=str(tmp_path))
        assert isinstance(mgr, IntentStateManager)
        assert mgr.intent == "unknown_intent"
