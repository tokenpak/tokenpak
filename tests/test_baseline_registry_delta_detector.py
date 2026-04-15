"""Tests for baseline registry and delta detector."""


import pytest
pytest.importorskip("tokenpak._internal", reason="module not available in current build")
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from tokenpak._internal.regression.baseline_registry import BaselineEntry, BaselineRegistry
from tokenpak._internal.regression.delta_detector import DeltaDetector, DeltaResult


@pytest.fixture
def temp_registry():
    """Create temporary registry for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = str(Path(tmpdir) / "baselines.json")
        yield registry_path


@pytest.fixture
def registry(temp_registry):
    """Create baseline registry instance."""
    return BaselineRegistry(registry_path=temp_registry)


@pytest.fixture
def baseline_entry():
    """Create sample baseline entry."""
    return BaselineEntry(
        workflow_id="workflow-001",
        artifact_hash="hash-abc123",
        artifact_snapshot={"summary": "Test output", "stats": {"lines": 100}},
        output_shape={"type": "dict", "keys": ["summary", "stats"]},
        validation_result={"passed": True, "checks": 5},
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat(),
        pass_count=1,
    )


@pytest.fixture
def detector():
    """Create delta detector instance."""
    return DeltaDetector(max_trivial_lines=15, max_trivial_files=2)


class TestBaselineEntry:
    """Test baseline entry schema."""

    def test_baseline_entry_creation(self, baseline_entry):
        """Test baseline entry creation."""
        assert baseline_entry.workflow_id == "workflow-001"
        assert baseline_entry.artifact_hash == "hash-abc123"
        assert baseline_entry.pass_count == 1

    def test_baseline_entry_to_dict(self, baseline_entry):
        """Test baseline entry serialization."""
        data = baseline_entry.to_dict()
        assert data["workflow_id"] == "workflow-001"
        assert data["pass_count"] == 1

    def test_baseline_entry_from_dict(self, baseline_entry):
        """Test baseline entry deserialization."""
        data = baseline_entry.to_dict()
        restored = BaselineEntry.from_dict(data)
        assert restored.workflow_id == baseline_entry.workflow_id
        assert restored.pass_count == baseline_entry.pass_count


class TestBaselineRegistry:
    """Test baseline registry CRUD operations."""

    def test_store_baseline(self, registry, baseline_entry):
        """Test storing a baseline."""
        registry.store_baseline(baseline_entry)
        assert baseline_entry.workflow_id in registry.baselines

    def test_retrieve_baseline(self, registry, baseline_entry):
        """Test retrieving a baseline."""
        registry.store_baseline(baseline_entry)
        retrieved = registry.get_baseline("workflow-001")

        assert retrieved is not None
        assert retrieved.artifact_hash == "hash-abc123"

    def test_retrieve_nonexistent_baseline(self, registry):
        """Test retrieving nonexistent baseline."""
        result = registry.get_baseline("nonexistent")
        assert result is None

    def test_update_pass_count(self, registry, baseline_entry):
        """Test updating baseline pass count."""
        registry.store_baseline(baseline_entry)
        registry.update_pass_count("workflow-001", increment=2)

        retrieved = registry.get_baseline("workflow-001")
        assert retrieved.pass_count == 3  # 1 (initial) + 2 (increment)

    def test_delete_baseline(self, registry, baseline_entry):
        """Test deleting a baseline."""
        registry.store_baseline(baseline_entry)
        registry.delete_baseline("workflow-001")

        result = registry.get_baseline("workflow-001")
        assert result is None

    def test_persistence(self, temp_registry, baseline_entry):
        """Test baseline persistence across restarts."""
        # Store in first registry instance
        registry1 = BaselineRegistry(registry_path=temp_registry)
        registry1.store_baseline(baseline_entry)

        # Create new instance (reload from disk)
        registry2 = BaselineRegistry(registry_path=temp_registry)
        retrieved = registry2.get_baseline("workflow-001")

        assert retrieved is not None
        assert retrieved.artifact_hash == "hash-abc123"

    def test_list_baselines(self, registry, baseline_entry):
        """Test listing all baselines."""
        registry.store_baseline(baseline_entry)

        all_baselines = registry.list_baselines()
        assert len(all_baselines) == 1
        assert "workflow-001" in all_baselines


class TestDeltaDetector:
    """Test delta detection."""

    def test_trivial_delta_no_changes(self, detector):
        """Test trivial delta when nothing changed."""
        state = {"lines": 100, "files": ["a.py", "b.py"], "dependencies": []}

        delta = detector.compute_delta(state, state)

        assert delta.is_trivial is True
        assert delta.magnitude == 0.0
        assert delta.changed_dimensions == []

    def test_trivial_delta_small_changes(self, detector):
        """Test trivial delta with small changes."""
        baseline = {
            "lines": 100,
            "files": ["a.py", "b.py"],
            "dependencies": ["dep1"],
            "config": {},
        }
        current = {
            "lines": 110,
            "files": ["a.py", "b.py", "c.py"],
            "dependencies": ["dep1"],
            "config": {},
        }

        delta = detector.compute_delta(current, baseline)

        assert delta.is_trivial is True
        assert "lines" in delta.changed_dimensions
        assert "files" in delta.changed_dimensions

    def test_moderate_delta(self, detector):
        """Test moderate delta detection."""
        baseline = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": ["dep1"],
            "config": {},
        }
        current = {
            "lines": 200,
            "files": ["a.py", "b.py", "c.py"],
            "dependencies": ["dep1", "dep2", "dep3"],
            "config": {"key1": "value1"},
        }

        delta = detector.compute_delta(current, baseline)

        assert delta.is_trivial is False
        assert delta.is_moderate is True
        assert delta.magnitude < 0.6

    def test_large_delta(self, detector):
        """Test large delta detection."""
        baseline = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }
        current = {
            "lines": 1000,
            "files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
            "dependencies": ["dep1", "dep2", "dep3", "dep4", "dep5"],
            "config": {"key1": "v1", "key2": "v2", "key3": "v3"},
        }

        delta = detector.compute_delta(current, baseline)

        assert delta.is_trivial is False
        assert delta.is_large is True
        assert delta.magnitude >= 0.6

    def test_delta_magnitude_bounds(self, detector):
        """Test delta magnitude is bounded [0, 1]."""
        baseline = {"lines": 0, "files": [], "dependencies": [], "config": {}}

        # Extreme change
        current = {
            "lines": 10000,
            "files": list(range(100)),
            "dependencies": list(range(100)),
            "config": {str(i): i for i in range(100)},
        }

        delta = detector.compute_delta(current, baseline)

        assert 0.0 <= delta.magnitude <= 1.0

    def test_delta_changed_dimensions(self, detector):
        """Test delta tracks changed dimensions."""
        baseline = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": ["dep1"],
            "config": {"key": "val"},
        }
        current = {
            "lines": 200,
            "files": ["b.py"],
            "dependencies": [],
            "config": {},
        }

        delta = detector.compute_delta(current, baseline)

        assert len(delta.changed_dimensions) > 0


class TestDeltaDecision:
    """Test decision logic based on delta."""

    def test_reuse_baseline_trivial_and_passes(self, detector):
        """Test reusing baseline when trivial and still passes."""
        baseline_state = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }
        current_state = {
            "lines": 105,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }

        delta = detector.compute_delta(current_state, baseline_state)
        should_reuse = detector.should_reuse_baseline(delta, baseline_still_passes=True)

        assert should_reuse is True

    def test_not_reuse_baseline_if_fails(self, detector):
        """Test not reusing baseline if it fails validation."""
        baseline_state = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }
        current_state = {
            "lines": 105,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }

        delta = detector.compute_delta(current_state, baseline_state)
        should_reuse = detector.should_reuse_baseline(
            delta, baseline_still_passes=False
        )

        assert should_reuse is False

    def test_validate_only_for_moderate_delta(self, detector):
        """Test validation-only for moderate deltas."""
        baseline_state = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": ["dep1"],
            "config": {},
        }
        current_state = {
            "lines": 200,
            "files": ["a.py", "b.py", "c.py"],
            "dependencies": ["dep1", "dep2"],
            "config": {"key": "val"},
        }

        delta = detector.compute_delta(current_state, baseline_state)
        should_validate = detector.should_validate_only(delta)

        assert should_validate is True

    def test_regenerate_for_large_delta(self, detector):
        """Test regeneration for large deltas."""
        baseline_state = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }
        current_state = {
            "lines": 1000,
            "files": ["a.py", "b.py", "c.py", "d.py"],
            "dependencies": list(range(10)),
            "config": {str(i): i for i in range(10)},
        }

        delta = detector.compute_delta(current_state, baseline_state)
        should_regen = detector.should_regenerate(delta)

        assert should_regen is True


class TestIntegration:
    """Integration tests for baseline + delta."""

    def test_baseline_workflow(self, registry, baseline_entry, detector):
        """Test complete baseline workflow."""
        # Store baseline
        registry.store_baseline(baseline_entry)

        # Simulate trivial change
        baseline_state = {
            "lines": 100,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }
        current_state = {
            "lines": 105,
            "files": ["a.py"],
            "dependencies": [],
            "config": {},
        }

        delta = detector.compute_delta(current_state, baseline_state)
        retrieved = registry.get_baseline("workflow-001")

        # Trivial delta + baseline passes → reuse
        should_reuse = detector.should_reuse_baseline(
            delta, baseline_still_passes=True
        )

        assert retrieved is not None
        assert should_reuse is True

    def test_baseline_update_after_regen(self, registry, baseline_entry, detector):
        """Test updating baseline after regeneration."""
        # Store initial baseline
        registry.store_baseline(baseline_entry)

        # After successful regen, update baseline
        new_entry = BaselineEntry(
            workflow_id="workflow-001",
            artifact_hash="hash-new-xyz789",
            artifact_snapshot={"summary": "New output", "stats": {"lines": 200}},
            output_shape={"type": "dict", "keys": ["summary", "stats"]},
            validation_result={"passed": True, "checks": 5},
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
            pass_count=2,
        )

        registry.store_baseline(new_entry)
        retrieved = registry.get_baseline("workflow-001")

        assert retrieved.artifact_hash == "hash-new-xyz789"
        assert retrieved.pass_count == 2
