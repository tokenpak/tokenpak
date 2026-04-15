"""Tests for TrackEdge feature and scoring engines."""

import pytest
import numpy as np

# trackedge is a separate project not installed in this environment — skip all tests here
trackedge = pytest.importorskip("trackedge.processing.feature_engine", reason="trackedge package not installed (separate project)")

from trackedge.processing.feature_engine import (
    speed_score, pace_style, race_pace_scenario, class_fit,
    workout_fitness, layoff_penalty, connections_score,
    first_time_starter_reweight, apply_shrinkage,
)
from trackedge.model.scoring_engine import (
    power_score, softmax_probabilities, race_confidence_score,
)


class TestFeatures:
    
    def test_speed_score_improving(self):
        horse = {"speed_ratings": [90, 85, 80]}
        result = speed_score(horse)
        assert result.score == pytest.approx(0.5*90 + 0.3*85 + 0.2*80, abs=0.1)
        assert result.trend == "improving"
    
    def test_speed_score_declining(self):
        horse = {"speed_ratings": [70, 80, 90]}
        result = speed_score(horse)
        assert result.trend == "declining"
    
    def test_speed_score_stable(self):
        horse = {"speed_ratings": [85, 85, 85]}
        result = speed_score(horse)
        assert result.trend == "stable"
    
    def test_pace_style_early(self):
        horse = {"avg_pace": 1.0, "avg_lenback": 0.5}
        assert pace_style(horse) == "E"
    
    def test_pace_style_stretch(self):
        horse = {"avg_pace": 5.0, "avg_lenback": 10.0}
        assert pace_style(horse) == "S"
    
    def test_race_pace_scenario_slow(self):
        """Test slow pace scenario (no early horses)."""
        race = {
            "horses": [
                {"id": "h1", "pace_style": "P"},
                {"id": "h2", "pace_style": "S"},
                {"id": "h3", "pace_style": "S"},
            ]
        }
        adj = race_pace_scenario(race)
        assert adj["h2"] == 1.05  # S gets boost in slow pace
        assert adj["h3"] == 1.05
    
    def test_race_pace_scenario_fast(self):
        race = {
            "horses": [
                {"id": "h1", "pace_style": "E"},
                {"id": "h2", "pace_style": "E"},
                {"id": "h3", "pace_style": "E"},
                {"id": "h4", "pace_style": "S"},
            ]
        }
        adj = race_pace_scenario(race)
        assert adj["h1"] < 1.0
        assert adj["h4"] > 1.0
    
    def test_class_fit_drop(self):
        horse = {"avg_class_rating": 50000}
        race = {"class_rating": 40000, "type": "Normal"}
        result = class_fit(horse, race)
        assert "purse_drop" in result.flags
        assert result.score <= 100
    
    def test_class_fit_claim_drop(self):
        horse = {"avg_class_rating": 50000}
        race = {"class_rating": 40000, "type": "Claim"}
        result = class_fit(horse, race)
        assert "claim_drop" in result.flags
    
    def test_workout_fitness_recent(self):
        horse = {
            "recent_workouts": [
                {"days_ago": 5, "distance": 5, "rank": 3},
            ]
        }
        result = workout_fitness(horse)
        assert result.score >= 80
        assert result.has_bullet == True
    
    def test_workout_fitness_stale(self):
        horse = {
            "recent_workouts": [
                {"days_ago": 60, "distance": 5, "rank": 10},
            ]
        }
        result = workout_fitness(horse)
        assert result.score < 70
    
    def test_layoff_penalty_short(self):
        horse = {"days_since_last_race": 20, "recent_workouts": []}
        assert layoff_penalty(horse) == 1.0
    
    def test_layoff_penalty_major(self):
        horse = {"days_since_last_race": 200, "recent_workouts": []}
        assert layoff_penalty(horse) < 1.0
    
    def test_connections_score(self):
        horse = {
            "jockey_win_rate": 0.20,
            "jockey_starts": 100,
            "trainer_win_rate": 0.25,
            "trainer_starts": 150,
        }
        score = connections_score(horse)
        assert 0 <= score <= 100
    
    def test_first_time_starter_reweight_no_starts(self):
        horse = {"starts": 0}
        weights = first_time_starter_reweight(horse)
        assert weights["form_fitness"] == 0.60
        assert weights["speed_score"] == 0.10
    
    def test_first_time_starter_reweight_one_start(self):
        horse = {"starts": 1}
        weights = first_time_starter_reweight(horse)
        assert weights["form_fitness"] == 0.40
    
    def test_first_time_starter_reweight_experienced(self):
        horse = {"starts": 5}
        weights = first_time_starter_reweight(horse)
        assert weights["speed_score"] == 0.35
    
    def test_shrinkage_low_sample(self):
        shrunk = apply_shrinkage(0.30, 5, baseline=0.15, k=7)
        assert shrunk < 0.30
        assert shrunk > 0.15
    
    def test_shrinkage_high_sample(self):
        shrunk = apply_shrinkage(0.30, 100, baseline=0.15, k=7)
        assert shrunk > 0.25


class TestScoring:
    
    def test_power_score_range(self):
        features = {
            "speed_score": 75,
            "class_fit": 80,
            "pace_fit": 70,
            "form_fitness": 85,
            "connections_score": 60,
        }
        score = power_score({}, {}, features)
        assert 0 <= score <= 100
    
    def test_power_score_weighted(self):
        features = {
            "speed_score": 100,
            "class_fit": 0,
            "pace_fit": 0,
            "form_fitness": 0,
            "connections_score": 0,
        }
        score = power_score({}, {}, features)
        assert score == pytest.approx(35, abs=1)
    
    def test_softmax_probabilities_sum_to_one(self):
        scores = {"h1": 75, "h2": 65, "h3": 55}
        probs = softmax_probabilities({}, scores, temperature=15.0)
        total = sum(probs.values())
        assert total == pytest.approx(1.0, abs=0.01)
    
    def test_softmax_probabilities_ordering(self):
        scores = {"h1": 90, "h2": 70, "h3": 50}
        probs = softmax_probabilities({}, scores, temperature=15.0)
        assert probs["h1"] > probs["h2"] > probs["h3"]
    
    def test_softmax_temperature_effect(self):
        scores = {"h1": 80, "h2": 80}
        probs_high_t = softmax_probabilities({}, scores, temperature=100.0)
        assert probs_high_t["h1"] == pytest.approx(0.5, abs=0.01)
    
    def test_race_confidence_data_quality(self):
        scores = {"h1": 70, "h2": 60}
        probs = softmax_probabilities({}, scores)
        
        race_full = {
            "horses": [
                {"id": "h1", "speed_ratings": [70]},
                {"id": "h2", "speed_ratings": [60]},
            ]
        }
        race_full["pace_adjustments"] = {}
        conf_full = race_confidence_score(race_full, scores, probs)
        
        race_partial = {
            "horses": [
                {"id": "h1"},
                {"id": "h2", "speed_ratings": [60]},
            ]
        }
        race_partial["pace_adjustments"] = {}
        conf_partial = race_confidence_score(race_partial, scores, probs)
        
        assert conf_full.data_quality > conf_partial.data_quality
    
    def test_softmax_no_division_by_zero(self):
        """Test softmax handles edge cases."""
        scores = {}
        probs = softmax_probabilities({}, scores)
        assert probs == {}
    
    def test_race_confidence_empty(self):
        """Test confidence with no probabilities."""
        race = {"horses": []}
        conf = race_confidence_score(race, {}, {})
        assert conf.level == "Low"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestPaceMetricsAndSpeed:
    """Tests for improved pace and field-relative speed."""
    
    def test_calculate_pace_metrics(self):
        """Test pace metrics calculation from comparable races."""
        from trackedge.processing.feature_engine import calculate_pace_metrics
        
        horse = {
            "past_performances": [
                {"pacefigure": 95, "lenback1": 2.0, "position1": 2, "position2": 3},
                {"pacefigure": 93, "lenback1": 2.5, "position1": 2, "position2": 4},
            ]
        }
        race = {"distance": 8.0, "class_rating": 50000, "surface": "dirt"}
        
        metrics = calculate_pace_metrics(horse, race)
        assert metrics["avg_pacefigure"] == pytest.approx(94.0, abs=0.1)
        assert metrics["avg_lenback1"] == pytest.approx(2.25, abs=0.1)
    
    def test_classify_pace_style_improved(self):
        """Test improved pace style classification."""
        from trackedge.processing.feature_engine import classify_pace_style_improved
        
        assert classify_pace_style_improved(1.0) == "E"
        assert classify_pace_style_improved(2.5) == "EP"
        assert classify_pace_style_improved(5.5) == "P"
        assert classify_pace_style_improved(8.0) == "S"
    
    def test_race_pace_projection(self):
        """Test race pace index calculation."""
        from trackedge.processing.feature_engine import race_pace_projection
        
        race = {
            "horses": [
                {"pace_metrics": {"avg_pacefigure": 95}},
                {"pace_metrics": {"avg_pacefigure": 93}},
                {"pace_metrics": {"avg_pacefigure": 90}},
                {"pace_metrics": {"avg_pacefigure": 88}},
            ]
        }
        
        result = race_pace_projection(race)
        assert "race_pace_index" in result
        assert "pace_label" in result
        assert result["pace_label"] in ["Slow", "Honest", "Fast", "Meltdown"]
    
    def test_pace_fit_adjustment_early_slow_pace(self):
        """Test pace fit adjustment for early horse in slow pace."""
        from trackedge.processing.feature_engine import pace_fit_adjustment
        
        horse = {"pace_style": "E"}
        adj = pace_fit_adjustment(horse, "Slow")
        assert adj == 2.0  # Boost for early in slow pace
    
    def test_pace_fit_adjustment_capping(self):
        """Test pace adjustment is capped -5 to +5."""
        from trackedge.processing.feature_engine import pace_fit_adjustment
        
        horse = {"pace_style": "P"}
        
        # All adjustments should be within bounds
        for label in ["Slow", "Honest", "Fast", "Meltdown"]:
            adj = pace_fit_adjustment(horse, label)
            assert -5 <= adj <= 5
    
    def test_speed_score_field_relative_normalization(self):
        """Test field-relative speed normalization."""
        from trackedge.processing.feature_engine import speed_score_field_relative
        
        # Weak field (all horses ~70 speed)
        race_weak = {
            "horses": [
                {
                    "past_performances": [
                        {"speedfigur": 70, "distance": 8.0, "class_rating": 40000, "surface": "dirt"},
                    ]
                },
                {
                    "past_performances": [
                        {"speedfigur": 72, "distance": 8.0, "class_rating": 40000, "surface": "dirt"},
                    ]
                },
            ]
        }
        
        horse = {
            "past_performances": [
                {"speedfigur": 90, "distance": 8.0, "class_rating": 40000, "surface": "dirt"},
            ]
        }
        
        score = speed_score_field_relative(horse, race_weak)
        # Even in weak field, 90 speed vs 71 field average should be well above 50
        assert score > 60
    
    def test_speed_score_field_relative_bounds(self):
        """Test speed score is bounded 20-90."""
        from trackedge.processing.feature_engine import speed_score_field_relative
        
        race = {
            "horses": [
                {"past_performances": [{"speedfigur": 100, "distance": 8.0, "class_rating": 50000, "surface": "dirt"}]},
                {"past_performances": [{"speedfigur": 95, "distance": 8.0, "class_rating": 50000, "surface": "dirt"}]},
            ]
        }
        
        # Very slow horse
        slow_horse = {"past_performances": [{"speedfigur": 30, "distance": 8.0, "class_rating": 50000, "surface": "dirt"}]}
        slow_score = speed_score_field_relative(slow_horse, race)
        assert 20 <= slow_score <= 90
        
        # Very fast horse
        fast_horse = {"past_performances": [{"speedfigur": 120, "distance": 8.0, "class_rating": 50000, "surface": "dirt"}]}
        fast_score = speed_score_field_relative(fast_horse, race)
        assert 20 <= fast_score <= 90
    
    def test_filter_comparable_races(self):
        """Test comparable race filtering."""
        from trackedge.processing.feature_engine import filter_comparable_races
        
        pps = [
            {"distance": 8.0, "class_rating": 50000, "surface": "dirt"},  # Comparable
            {"distance": 6.0, "class_rating": 50000, "surface": "dirt"},  # Wrong distance
            {"distance": 8.0, "class_rating": 70000, "surface": "dirt"},  # Wrong class
            {"distance": 8.0, "class_rating": 50000, "surface": "turf"},  # Wrong surface
        ]
        
        race = {"distance": 8.0, "class_rating": 50000, "surface": "dirt"}
        comparable = filter_comparable_races(pps, race)
        
        assert len(comparable) == 1
        assert comparable[0]["distance"] == 8.0

