# SPDX-License-Identifier: Apache-2.0
"""Unit tests for elo.py — Elo rating system."""

import json
import pytest
from pathlib import Path

from tokenpak.telemetry.elo import (
    EloRatings,
    INITIAL_RATING,
    K_FACTOR,
    _BENCHMARK_RATING,
    get_elo,
    update_elo,
)


class TestEloRatingsClass:
    """Test the EloRatings class."""

    def test_get_elo_returns_default_for_unseen_model(self, tmp_path):
        """Verify get_elo returns INITIAL_RATING for new (model, task_type) pair."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        rating = elo.get_elo("gpt-4", "classification")
        assert rating == INITIAL_RATING

    def test_update_elo_increases_on_acceptance(self, tmp_path):
        """Verify update_elo increases rating when accepted=True."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        initial = elo.get_elo("gpt-4", "classification")
        updated = elo.update_elo("gpt-4", "classification", accepted=True)
        assert updated > initial

    def test_update_elo_decreases_on_rejection(self, tmp_path):
        """Verify update_elo decreases rating when accepted=False."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        initial = elo.get_elo("claude-3", "summarization")
        updated = elo.update_elo("claude-3", "summarization", accepted=False)
        assert updated < initial

    def test_multiple_models_tracked_independently(self, tmp_path):
        """Verify different models maintain independent ratings for same task_type."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        
        # Update model-1 with acceptance
        elo.update_elo("model-1", "task-a", accepted=True)
        rating_1 = elo.get_elo("model-1", "task-a")
        
        # Update model-2 with rejection
        elo.update_elo("model-2", "task-a", accepted=False)
        rating_2 = elo.get_elo("model-2", "task-a")
        
        # Both should differ from initial and from each other
        assert rating_1 > INITIAL_RATING
        assert rating_2 < INITIAL_RATING
        assert rating_1 != rating_2

    def test_multiple_task_types_tracked_independently(self, tmp_path):
        """Verify different task_types maintain independent ratings for same model."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        
        # Update same model with different task_types
        elo.update_elo("claude-3", "task-1", accepted=True)
        elo.update_elo("claude-3", "task-2", accepted=False)
        
        rating_task1 = elo.get_elo("claude-3", "task-1")
        rating_task2 = elo.get_elo("claude-3", "task-2")
        
        assert rating_task1 > INITIAL_RATING
        assert rating_task2 < INITIAL_RATING
        assert rating_task1 != rating_task2

    def test_persistence_save_and_load(self, tmp_path):
        """Verify ratings persist to file and load correctly."""
        elo_path = str(tmp_path / "elo.json")
        
        # Create and update ratings
        elo1 = EloRatings(elo_path)
        elo1.update_elo("model-x", "task-x", accepted=True)
        elo1.update_elo("model-y", "task-y", accepted=False)
        rating1_x = elo1.get_elo("model-x", "task-x")
        rating1_y = elo1.get_elo("model-y", "task-y")
        
        # Create new instance from same file
        elo2 = EloRatings(elo_path)
        rating2_x = elo2.get_elo("model-x", "task-x")
        rating2_y = elo2.get_elo("model-y", "task-y")
        
        assert rating2_x == rating1_x
        assert rating2_y == rating1_y

    def test_file_io_with_corrupted_json(self, tmp_path):
        """Verify graceful handling of corrupted JSON file."""
        elo_path = str(tmp_path / "elo.json")
        Path(elo_path).write_text("{ invalid json }")
        
        # Should not raise; should load empty dict
        elo = EloRatings(elo_path)
        rating = elo.get_elo("model-a", "task-a")
        assert rating == INITIAL_RATING

    def test_file_io_with_missing_parent_directory(self, tmp_path):
        """Verify parent directories are created on save."""
        elo_path = str(tmp_path / "nested" / "deep" / "elo.json")
        elo = EloRatings(elo_path)
        elo.update_elo("model-z", "task-z", accepted=True)
        
        # Should have created parent dirs and saved file
        assert Path(elo_path).exists()

    def test_get_all_returns_copy(self, tmp_path):
        """Verify get_all returns a copy, not a reference."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        elo.update_elo("model-1", "task-1", accepted=True)
        
        all_ratings = elo.get_all()
        all_ratings["model-1::task-1"] = 9999.0  # Modify the copy
        
        # Original should be unchanged
        assert elo.get_elo("model-1", "task-1") != 9999.0

    def test_reset_clears_all(self, tmp_path):
        """Verify reset() with no args clears all ratings."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        elo.update_elo("model-1", "task-1", accepted=True)
        elo.update_elo("model-2", "task-2", accepted=True)
        
        elo.reset()
        assert elo.get_elo("model-1", "task-1") == INITIAL_RATING
        assert elo.get_elo("model-2", "task-2") == INITIAL_RATING

    def test_reset_selective_by_model(self, tmp_path):
        """Verify reset(model=...) clears only that model."""
        elo = EloRatings(str(tmp_path / "elo.json"))
        elo.update_elo("model-1", "task-1", accepted=True)
        elo.update_elo("model-2", "task-1", accepted=True)
        
        rating_1_before = elo.get_elo("model-1", "task-1")
        rating_2_before = elo.get_elo("model-2", "task-1")
        assert rating_1_before > INITIAL_RATING
        assert rating_2_before > INITIAL_RATING
        
        elo.reset(model="model-1")
        assert elo.get_elo("model-1", "task-1") == INITIAL_RATING
        assert elo.get_elo("model-2", "task-1") == rating_2_before  # Unchanged


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    def test_get_elo_function(self, tmp_path):
        """Test module-level get_elo function."""
        elo_path = str(tmp_path / "elo.json")
        rating = get_elo("gpt-4", "task-x", elo_path=elo_path)
        assert rating == INITIAL_RATING

    def test_update_elo_function(self, tmp_path):
        """Test module-level update_elo function."""
        elo_path = str(tmp_path / "elo.json")
        initial = get_elo("claude-3", "task-y", elo_path=elo_path)
        updated = update_elo("claude-3", "task-y", accepted=True, elo_path=elo_path)
        assert updated > initial

    def test_module_functions_use_default_path(self, tmp_path, monkeypatch):
        """Test that module functions use the default path when not specified."""
        # This test verifies the default path logic works
        # (limited by not mocking filesystem; just verify it doesn't crash)
        rating = get_elo("test-model", "test-task")
        assert isinstance(rating, float)
        assert rating == INITIAL_RATING
