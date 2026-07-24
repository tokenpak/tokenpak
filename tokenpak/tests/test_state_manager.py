"""Unit tests for StateManager and IntentStateManager classes."""

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.state_manager import (
    IntentStateManager,
    MultiSchemaStateManager,
    StateManager,
    select_state_manager,
)


class TestStateManager:
    """Tests for the core StateManager class."""

    def test_init_creates_state_directory(self):
        """StateManager should create .tpk/state directory on init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = tmpdir
            mgr = StateManager("test-session-1", base_dir=base_dir)
            state_dir = Path(base_dir) / "state"
            assert state_dir.exists()
            assert state_dir.is_dir()

    def test_init_empty_state(self):
        """StateManager should initialize with empty state structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            assert mgr.state == {
                "goal": "",
                "constraints": [],
                "current_task": "",
                "done": [],
                "open": [],
                "next": [],
                "defs": {},
            }

    def test_load_creates_state_path(self):
        """StateManager should set state_path correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            expected_path = Path(tmpdir) / "state" / "session_test-session-1.state.json"
            assert mgr.state_path == expected_path

    def test_save_and_load(self):
        """StateManager should persist and load state from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_goal("Test goal")
            mgr.set_current_task("Test task")
            mgr.add_open("item1")
            mgr.save()

            # Load in a new instance
            mgr2 = StateManager("test-session-1", base_dir=tmpdir)
            assert mgr2.state["goal"] == "Test goal"
            assert mgr2.state["current_task"] == "Test task"
            assert "item1" in mgr2.state["open"]

    def test_set_goal(self):
        """set_goal should update the goal field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_goal("New goal")
            assert mgr.state["goal"] == "New goal"

    def test_set_current_task(self):
        """set_current_task should update the current_task field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_current_task("New task")
            assert mgr.state["current_task"] == "New task"

    def test_mark_done_moves_from_open(self):
        """mark_done should move item from open to done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_open("item1")
            assert "item1" in mgr.state["open"]
            mgr.mark_done("item1")
            assert "item1" not in mgr.state["open"]
            assert "item1" in mgr.state["done"]

    def test_mark_done_appends_if_not_in_open(self):
        """mark_done should append to done even if not in open."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.mark_done("item1")
            assert "item1" in mgr.state["done"]

    def test_mark_done_idempotent(self):
        """mark_done should be idempotent (no duplicate done entries)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.mark_done("item1")
            mgr.mark_done("item1")
            assert mgr.state["done"].count("item1") == 1

    def test_add_open(self):
        """add_open should add items to the open list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_open("item1")
            mgr.add_open("item2")
            assert mgr.state["open"] == ["item1", "item2"]

    def test_add_next(self):
        """add_next should add items to the next queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_next("next_item_1")
            assert mgr.state["next"] == ["next_item_1"]

    def test_add_constraint(self):
        """add_constraint should add constraints to the list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_constraint("Constraint 1")
            mgr.add_constraint("Constraint 2")
            assert mgr.state["constraints"] == ["Constraint 1", "Constraint 2"]

    def test_set_def(self):
        """set_def should store key-value definitions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_def("key1", "value1")
            mgr.set_def("key2", 42)
            assert mgr.state["defs"]["key1"] == "value1"
            assert mgr.state["defs"]["key2"] == 42

    def test_to_wire_format(self):
        """to_wire_format should produce compact JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_goal("Test")
            wire = mgr.to_wire_format()
            assert isinstance(wire, str)
            assert wire.startswith("{")
            # Should be compact (no spaces after separators)
            assert ", " not in wire
            # Should be valid JSON
            parsed = json.loads(wire)
            assert parsed["goal"] == "Test"

    def test_to_wire_section(self):
        """to_wire_section should include STATE_JSON prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.set_goal("Test")
            section = mgr.to_wire_section()
            assert section.startswith("STATE_JSON:\n")
            assert "goal" in section

    def test_apply_patch_add_operation(self):
        """apply_patch with ADD op should add to list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            patch = {"op": "ADD", "path": "open", "value": "item1"}
            mgr.apply_patch(patch)
            assert "item1" in mgr.state["open"]

    def test_apply_patch_add_no_duplicate(self):
        """apply_patch ADD should not create duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_open("item1")
            patch = {"op": "ADD", "path": "open", "value": "item1"}
            mgr.apply_patch(patch)
            assert mgr.state["open"].count("item1") == 1

    def test_apply_patch_remove_operation(self):
        """apply_patch with REMOVE op should remove from list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            mgr.add_open("item1")
            mgr.add_open("item2")
            patch = {"op": "REMOVE", "path": "open", "value": "item1"}
            mgr.apply_patch(patch)
            assert "item1" not in mgr.state["open"]
            assert "item2" in mgr.state["open"]

    def test_apply_patch_set_operation(self):
        """apply_patch with SET op should set scalar values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            patch = {"op": "SET", "path": "goal", "value": "New goal"}
            mgr.apply_patch(patch)
            assert mgr.state["goal"] == "New goal"

    def test_apply_patch_unknown_op_raises(self):
        """apply_patch with unknown op should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            patch = {"op": "INVALID", "path": "goal", "value": "test"}
            with pytest.raises(ValueError, match="Unknown patch op"):
                mgr.apply_patch(patch)

    def test_validate_missing_file(self):
        """validate should pass if jsonschema not available (fallback)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-session-1", base_dir=tmpdir)
            # Should not raise even if schema file missing
            mgr.validate()

    def test_load_corrupted_json_reinits(self):
        """load should reinit state if JSON on disk is corrupted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir()
            state_file = state_dir / "session_test-session-1.state.json"
            state_file.write_text("{invalid json}")

            mgr = StateManager("test-session-1", base_dir=tmpdir)
            # Should have reinitialized to empty state
            assert mgr.state["goal"] == ""
            assert mgr.state["current_task"] == ""


class TestIntentStateManager:
    """Tests for IntentStateManager (intent-specific state)."""

    def test_init_creates_intent_state_file(self):
        """IntentStateManager should create intent-specific state files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            expected_path = Path(tmpdir) / "state" / "session_session-1.debug.state.json"
            assert mgr.state_path == expected_path

    def test_init_with_defaults_debug(self):
        """IntentStateManager should initialize with intent-specific defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            assert mgr.state["error"] == ""
            assert mgr.state["affected_files"] == []
            assert mgr.state["failing_tests"] == []

    def test_init_with_defaults_plan(self):
        """IntentStateManager for 'plan' intent should have plan defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "plan", base_dir=tmpdir)
            assert "objective" in mgr.state
            assert "blockers" in mgr.state

    def test_set_and_get(self):
        """set/get should store and retrieve values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            mgr.set("error", "ValueError in parse.py")
            assert mgr.get("error") == "ValueError in parse.py"

    def test_get_default(self):
        """get should return default if key missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            result = mgr.get("nonexistent_key", "default_value")
            assert result == "default_value"

    def test_update_patches_state(self):
        """update should shallow-merge a dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            mgr.update({"error": "New error", "changed_files": ["a.py", "b.py"]})
            assert mgr.state["error"] == "New error"
            assert mgr.state["changed_files"] == ["a.py", "b.py"]

    def test_save_and_load_intent_state(self):
        """IntentStateManager should persist and load intent state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            mgr.set("error", "RuntimeError: out of memory")
            mgr.save()

            mgr2 = IntentStateManager("session-1", "debug", base_dir=tmpdir)
            assert mgr2.get("error") == "RuntimeError: out of memory"

    def test_to_wire_format_intent(self):
        """to_wire_format should produce compact JSON for intent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "plan", base_dir=tmpdir)
            mgr.set("objective", "Migrate DB")
            wire = mgr.to_wire_format()
            parsed = json.loads(wire)
            assert parsed["objective"] == "Migrate DB"
            assert "," not in wire.replace(",", "")  # no space after commas

    def test_to_wire_section_intent(self):
        """to_wire_section should include intent tag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = IntentStateManager("session-1", "plan", base_dir=tmpdir)
            mgr.set("objective", "Test")
            section = mgr.to_wire_section()
            assert section.startswith("STATE_JSON[plan]:")

    def test_repr(self):
        """__repr__ should show session and intent."""
        mgr = IntentStateManager("session-1", "debug")
        repr_str = repr(mgr)
        assert "session-1" in repr_str
        assert "debug" in repr_str


class TestMultiSchemaStateManager:
    """Tests for MultiSchemaStateManager (manages multiple intents)."""

    def test_for_intent_creates_manager(self):
        """for_intent should create or return IntentStateManager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            debug_mgr = mgr.for_intent("debug")
            assert isinstance(debug_mgr, IntentStateManager)
            assert debug_mgr.intent == "debug"

    def test_for_intent_reuses_manager(self):
        """for_intent should reuse existing IntentStateManager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            debug_mgr1 = mgr.for_intent("debug")
            debug_mgr2 = mgr.for_intent("debug")
            assert debug_mgr1 is debug_mgr2

    def test_multiple_intents(self):
        """MultiSchemaStateManager can manage multiple intents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            debug_mgr = mgr.for_intent("debug")
            plan_mgr = mgr.for_intent("plan")

            debug_mgr.set("error", "Error1")
            plan_mgr.set("objective", "Objective1")

            assert debug_mgr.get("error") == "Error1"
            assert plan_mgr.get("objective") == "Objective1"

    def test_save_all(self):
        """save_all should persist all active intent states."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            mgr.for_intent("debug").set("error", "Error1")
            mgr.for_intent("plan").set("objective", "Obj1")
            mgr.save_all()

            # Verify files exist
            state_dir = Path(tmpdir) / "state"
            assert (state_dir / "session_session-1.debug.state.json").exists()
            assert (state_dir / "session_session-1.plan.state.json").exists()

    def test_active_intents(self):
        """active_intents should return list of active intents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            mgr.for_intent("debug")
            mgr.for_intent("plan")
            mgr.for_intent("execute")

            intents = mgr.active_intents()
            assert len(intents) == 3
            assert "debug" in intents
            assert "plan" in intents
            assert "execute" in intents

    def test_build_wire_section(self):
        """build_wire_section should create tagged wire section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            mgr.for_intent("debug").set("error", "TestError")
            section = mgr.build_wire_section("debug")
            assert section.startswith("STATE_JSON[debug]:")

    def test_repr(self):
        """__repr__ should show session and active intents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = MultiSchemaStateManager("session-1", base_dir=tmpdir)
            mgr.for_intent("debug")
            repr_str = repr(mgr)
            assert "session-1" in repr_str
            assert "debug" in repr_str


class TestSelectStateManager:
    """Tests for the select_state_manager factory function."""

    def test_select_state_manager_factory(self):
        """select_state_manager should return IntentStateManager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = select_state_manager("session-1", "debug", base_dir=tmpdir)
            assert isinstance(mgr, IntentStateManager)
            assert mgr.intent == "debug"
            assert mgr.session_id == "session-1"

    def test_select_multiple_intents(self):
        """select_state_manager should work for all intent types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            intents = ["debug", "create", "plan", "execute", "query", "search"]
            for intent in intents:
                mgr = select_state_manager("session-1", intent, base_dir=tmpdir)
                assert mgr.intent == intent


class TestEdgeCases:
    """Edge cases and integration tests."""

    def test_empty_state_initialization(self):
        """StateManager with no arguments should initialize cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-1", base_dir=tmpdir)
            assert isinstance(mgr.state, dict)
            assert len(mgr.state) > 0

    def test_none_values_in_state(self):
        """StateManager should handle None values gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-1", base_dir=tmpdir)
            mgr.set_def("nullable", None)
            assert mgr.state["defs"]["nullable"] is None

    def test_special_characters_in_values(self):
        """StateManager should handle special characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-1", base_dir=tmpdir)
            mgr.set_goal('Goal with \\n newline and "quotes"')
            mgr.save()

            mgr2 = StateManager("test-1", base_dir=tmpdir)
            assert "newline" in mgr2.state["goal"]
            assert "quotes" in mgr2.state["goal"]

    def test_large_state_serialization(self):
        """StateManager should handle larger state objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = StateManager("test-1", base_dir=tmpdir)
            # Add a lot of items
            for i in range(100):
                mgr.add_open(f"item_{i}")
                mgr.add_constraint(f"constraint_{i}")

            assert len(mgr.state["open"]) == 100
            wire = mgr.to_wire_format()
            assert len(wire) > 1000  # Should be a substantial JSON string
            parsed = json.loads(wire)  # Should still be valid JSON
            assert len(parsed["open"]) == 100
