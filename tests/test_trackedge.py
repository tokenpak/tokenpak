"""
Tests for TrackEdge Feature Engine, Scoring Engine, and Probability Engine.
"""

import math
import pytest
import numpy as np
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trackedge.processing.feature_engine import (
    speed_score,
    pace_style,
    race_pace_scenario,
    class_fit,
    workout_fitness,
    layoff_penalty,
    connections_score,
    first_time_starter_reweight,
    apply_shrinkage,
)
from trackedge.model.scoring_engine import (
    power_score,
    race_confidence_score,
)
from trackedge.model.probability import softmax_probabilities, top_contenders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_horse(**kwargs):
    defaults = {
        "id": "h1",
        "speed_ratings": [85, 80, 78],
        "avg_pace": 2.5,
        "avg_lenback": 3.0,
        "pace_style": "EP",
        "avg_class_rating": 50000,
        "days_since_last_race": 14,
        "recent_workouts": [
            {"days_ago": 5, "rank": 3},
            {"days_ago": 12, "rank": 10},
        ],
        "jockey_win_rate": 0.18,
        "trainer_win_rate": 0.20,
        "jockey_starts": 50,
        "trainer_starts": 100,
        "starts": 5,
    }
    defaults.update(kwargs)
    return defaults


def make_race(**kwargs):
    defaults = {
        "id": "r1",
        "class_rating": 50000,
        "type": "Normal",
        "horses": [
            {"id": "h1", "pace_style": "E", "speed_ratings": [85]},
            {"id": "h2", "pace_style": "P", "speed_ratings": [80]},
            {"id": "h3", "pace_style": "S", "speed_ratings": [75]},
        ],
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# 8.1 Speed Score
# ---------------------------------------------------------------------------

class TestSpeedScore:
    def test_weighted_average(self):
        horse = make_horse(speed_ratings=[90, 80, 70])
        sr = speed_score(horse)
        expected = 0.5 * 90 + 0.3 * 80 + 0.2 * 70
        assert math.isclose(sr.score, expected, rel_tol=1e-6)

    def test_improving_trend(self):
        horse = make_horse(speed_ratings=[90, 80, 70])
        sr = speed_score(horse)
        assert sr.trend == "improving"

    def test_declining_trend(self):
        horse = make_horse(speed_ratings=[70, 80, 90])
        sr = speed_score(horse)
        assert sr.trend == "declining"

    def test_stable_trend(self):
        horse = make_horse(speed_ratings=[80, 80, 80])
        sr = speed_score(horse)
        assert sr.trend == "stable"

    def test_missing_ratings_pads(self):
        horse = make_horse(speed_ratings=[])
        sr = speed_score(horse)
        assert sr.score == 0.0

    def test_partial_ratings(self):
        horse = make_horse(speed_ratings=[85])
        sr = speed_score(horse)
        assert sr.score == 0.5 * 85

    def test_returns_float(self):
        horse = make_horse()
        sr = speed_score(horse)
        assert isinstance(sr.score, float)


# ---------------------------------------------------------------------------
# 8.2 Pace Style
# ---------------------------------------------------------------------------

class TestPaceStyle:
    def test_early(self):
        assert pace_style(make_horse(avg_pace=0.5, avg_lenback=0.5)) == "E"

    def test_early_pace(self):
        assert pace_style(make_horse(avg_pace=2.0, avg_lenback=3.0)) == "EP"

    def test_pace(self):
        assert pace_style(make_horse(avg_pace=3.5, avg_lenback=7.0)) == "P"

    def test_stretch(self):
        assert pace_style(make_horse(avg_pace=6.0, avg_lenback=10.0)) == "S"

    def test_returns_string(self):
        result = pace_style(make_horse())
        assert isinstance(result, str)
        assert result in ["E", "EP", "P", "S"]


# ---------------------------------------------------------------------------
# 8.3 Race Pace Scenario
# ---------------------------------------------------------------------------

class TestRacePaceScenario:
    def test_no_early_horses(self):
        race = make_race(horses=[
            {"id": "h1", "pace_style": "P"},
            {"id": "h2", "pace_style": "S"},
        ])
        # Annotate pace_style before calling
        adjustments = race_pace_scenario(race)
        # P and S both get a 1.05 boost when no early horses (slow pace benefits all runners)
        assert adjustments["h1"] == pytest.approx(1.05)
        assert adjustments["h2"] == pytest.approx(1.05)

    def test_honest_pace(self):
        race = make_race(horses=[
            {"id": "h1", "pace_style": "E"},
            {"id": "h2", "pace_style": "P"},
        ])
        adjustments = race_pace_scenario(race)
        assert all(v == 1.0 for v in adjustments.values())

    def test_fast_pace_penalizes_early(self):
        race = make_race(horses=[
            {"id": "h1", "pace_style": "E"},
            {"id": "h2", "pace_style": "E"},
            {"id": "h3", "pace_style": "E"},
            {"id": "h4", "pace_style": "S"},
        ])
        adjustments = race_pace_scenario(race)
        assert adjustments["h1"] == pytest.approx(0.95)
        assert adjustments["h4"] == pytest.approx(1.10)

    def test_returns_all_horses(self):
        race = make_race()
        adjustments = race_pace_scenario(race)
        assert len(adjustments) == len(race["horses"])


# ---------------------------------------------------------------------------
# 8.4 Class Fit
# ---------------------------------------------------------------------------

class TestClassFit:
    def test_neutral_same_class(self):
        horse = make_horse(avg_class_rating=50000)
        race = make_race(class_rating=50000)
        result = class_fit(horse, race)
        assert "neutral" in result.flags

    def test_purse_drop(self):
        horse = make_horse(avg_class_rating=60000)
        race = make_race(class_rating=50000)
        result = class_fit(horse, race)
        assert "purse_drop" in result.flags

    def test_class_raise(self):
        horse = make_horse(avg_class_rating=45000)
        race = make_race(class_rating=60000)
        result = class_fit(horse, race)
        assert "class_raise" in result.flags

    def test_score_in_range(self):
        horse = make_horse()
        race = make_race()
        result = class_fit(horse, race)
        assert 0 <= result.score <= 100

    def test_no_division_by_zero(self):
        horse = make_horse(avg_class_rating=50000)
        race = make_race(class_rating=0)
        result = class_fit(horse, race)  # Should not raise
        assert isinstance(result.score, float)


# ---------------------------------------------------------------------------
# 8.5 Workout Fitness
# ---------------------------------------------------------------------------

class TestWorkoutFitness:
    def test_fresh_bullet(self):
        horse = make_horse(recent_workouts=[
            {"days_ago": 3, "rank": 1},
            {"days_ago": 10, "rank": 2},
        ])
        wf = workout_fitness(horse)
        assert wf.has_bullet is True
        assert wf.score >= 85

    def test_no_workouts(self):
        horse = make_horse(recent_workouts=[])
        wf = workout_fitness(horse)
        assert wf.score == 50.0
        assert wf.has_bullet is False
        assert wf.days_since_last == 365

    def test_old_workout_lower_score(self):
        horse = make_horse(recent_workouts=[{"days_ago": 90, "rank": 5}])
        wf = workout_fitness(horse)
        assert wf.score < 70

    def test_score_capped_100(self):
        horse = make_horse(recent_workouts=[
            {"days_ago": 3, "rank": 1},
            {"days_ago": 7, "rank": 2},
            {"days_ago": 14, "rank": 3},
        ])
        wf = workout_fitness(horse)
        assert wf.score <= 100


# ---------------------------------------------------------------------------
# 8.6 Layoff Penalty
# ---------------------------------------------------------------------------

class TestLayoffPenalty:
    def test_recent_no_penalty(self):
        horse = make_horse(days_since_last_race=10, recent_workouts=[])
        assert layoff_penalty(horse) == 1.0

    def test_mild_penalty_no_workouts(self):
        horse = make_horse(days_since_last_race=45, recent_workouts=[])
        penalty = layoff_penalty(horse)
        assert penalty == pytest.approx(0.95)

    def test_mild_mitigated_by_bullet(self):
        horse = make_horse(
            days_since_last_race=45,
            recent_workouts=[{"days_ago": 10, "rank": 2}],
        )
        assert layoff_penalty(horse) == 1.0

    def test_major_penalty_long_layoff(self):
        horse = make_horse(days_since_last_race=200, recent_workouts=[])
        assert layoff_penalty(horse) == pytest.approx(0.50)

    def test_long_layoff_bullets_partial_mitigate(self):
        horse = make_horse(
            days_since_last_race=200,
            recent_workouts=[
                {"days_ago": 5, "rank": 1},
                {"days_ago": 12, "rank": 2},
            ],
        )
        assert layoff_penalty(horse) == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# 8.7 Connections Score
# ---------------------------------------------------------------------------

class TestConnectionsScore:
    def test_score_in_range(self):
        horse = make_horse()
        score = connections_score(horse)
        assert 0 <= score <= 100

    def test_elite_connections(self):
        horse = make_horse(
            jockey_win_rate=0.30,
            trainer_win_rate=0.30,
            jockey_starts=200,
            trainer_starts=200,
        )
        assert connections_score(horse) > 50

    def test_low_starts_shrinkage(self):
        """With 0 starts, result should regress to baseline."""
        horse = make_horse(
            jockey_win_rate=0.50,
            trainer_win_rate=0.50,
            jockey_starts=0,
            trainer_starts=0,
        )
        # Should be pulled toward baseline (0.15/0.18), not stay at 0.50
        score = connections_score(horse)
        assert score < 75  # Not at 50*2.5=125 cap


# ---------------------------------------------------------------------------
# 8.8 First-Time Starter Reweight
# ---------------------------------------------------------------------------

class TestFirstTimeStarterReweight:
    def test_zero_starts(self):
        horse = make_horse(starts=0)
        weights = first_time_starter_reweight(horse)
        assert weights["form_fitness"] == pytest.approx(0.60)
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_one_start(self):
        horse = make_horse(starts=1)
        weights = first_time_starter_reweight(horse)
        assert weights["form_fitness"] > 0.20
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_normal_horse(self):
        horse = make_horse(starts=5)
        weights = first_time_starter_reweight(horse)
        assert weights["speed_score"] == pytest.approx(0.35)

    def test_weights_sum_to_one_all_cases(self):
        for starts in [0, 1, 5, 20]:
            horse = make_horse(starts=starts)
            weights = first_time_starter_reweight(horse)
            assert abs(sum(weights.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Shrinkage
# ---------------------------------------------------------------------------

class TestApplyShrinkage:
    def test_zero_starts_returns_baseline(self):
        assert apply_shrinkage(0.30, 0) == pytest.approx(0.12)

    def test_large_sample_approaches_stat(self):
        result = apply_shrinkage(0.25, 1000)
        assert abs(result - 0.25) < 0.01

    def test_small_sample_pulled_toward_baseline(self):
        result = apply_shrinkage(0.40, 3)
        assert result < 0.30

    def test_result_between_stat_and_baseline(self):
        stat, baseline = 0.40, 0.12
        result = apply_shrinkage(stat, 7)
        assert min(stat, baseline) <= result <= max(stat, baseline)


# ---------------------------------------------------------------------------
# Power Score
# ---------------------------------------------------------------------------

class TestPowerScore:
    def test_output_in_range(self):
        horse = make_horse()
        race = make_race()
        features = {
            "speed_score": 80,
            "class_fit": 70,
            "pace_fit": 75,
            "form_fitness": 65,
            "connections_score": 60,
        }
        score = power_score(horse, race, features)
        assert 0 <= score <= 100

    def test_perfect_score(self):
        horse = make_horse()
        race = make_race()
        features = {k: 100 for k in ["speed_score", "class_fit", "pace_fit", "form_fitness", "connections_score"]}
        assert power_score(horse, race, features) == pytest.approx(100.0)

    def test_zero_score(self):
        horse = make_horse()
        race = make_race()
        features = {k: 0 for k in ["speed_score", "class_fit", "pace_fit", "form_fitness", "connections_score"]}
        assert power_score(horse, race, features) == pytest.approx(0.0)

    def test_weighted_correctly(self):
        horse, race = make_horse(), make_race()
        features = {
            "speed_score": 100,
            "class_fit": 0,
            "pace_fit": 0,
            "form_fitness": 0,
            "connections_score": 0,
        }
        # Only speed: 0.35 * 100 = 35
        assert power_score(horse, race, features) == pytest.approx(35.0)

    def test_missing_features_handled(self):
        horse, race = make_horse(), make_race()
        # All missing → defaults to 50 → 50
        score = power_score(horse, race, {})
        assert score == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Softmax Probabilities
# ---------------------------------------------------------------------------

class TestSoftmaxProbabilities:
    def test_sums_to_one(self):
        scores = {"h1": 80, "h2": 70, "h3": 65, "h4": 60}
        probs = softmax_probabilities(scores)
        assert abs(sum(probs.values()) - 1.0) < 1e-9

    def test_higher_score_higher_prob(self):
        scores = {"h1": 90, "h2": 50}
        probs = softmax_probabilities(scores)
        assert probs["h1"] > probs["h2"]

    def test_equal_scores_uniform(self):
        scores = {"h1": 75, "h2": 75, "h3": 75}
        probs = softmax_probabilities(scores)
        for p in probs.values():
            assert abs(p - 1 / 3) < 1e-6

    def test_single_horse(self):
        probs = softmax_probabilities({"h1": 80})
        assert probs["h1"] == pytest.approx(1.0)

    def test_empty_field(self):
        assert softmax_probabilities({}) == {}

    def test_nan_handling(self):
        scores = {"h1": float("nan"), "h2": 80}
        probs = softmax_probabilities(scores)
        # Should not raise; probabilities must be valid
        assert abs(sum(probs.values()) - 1.0) < 1e-6
        assert all(0 <= p <= 1 for p in probs.values())

    def test_all_nan(self):
        scores = {"h1": float("nan"), "h2": float("nan")}
        probs = softmax_probabilities(scores)
        assert abs(sum(probs.values()) - 1.0) < 1e-6

    def test_probabilities_between_zero_and_one(self):
        scores = {f"h{i}": float(i * 10) for i in range(1, 11)}
        probs = softmax_probabilities(scores)
        assert all(0 <= p <= 1 for p in probs.values())

    def test_large_field(self):
        scores = {f"h{i}": float(i) for i in range(1, 21)}
        probs = softmax_probabilities(scores)
        assert abs(sum(probs.values()) - 1.0) < 1e-9

    def test_top_contenders(self):
        scores = {"h1": 90, "h2": 85, "h3": 70, "h4": 60}
        probs = softmax_probabilities(scores)
        top = top_contenders(probs, n=2)
        assert top[0][0] == "h1"
        assert top[1][0] == "h2"


# ---------------------------------------------------------------------------
# Race Confidence Score
# ---------------------------------------------------------------------------

class TestRaceConfidenceScore:
    def test_returns_valid_level(self):
        race = make_race()
        scores = {"h1": 85, "h2": 70, "h3": 60}
        probs = softmax_probabilities(scores)
        rc = race_confidence_score(race, scores, probs)
        assert rc.level in ["High", "Medium", "Low"]

    def test_dominant_favorite_high_confidence(self):
        race = make_race()
        scores = {"h1": 99, "h2": 30, "h3": 25}
        probs = softmax_probabilities(scores)
        rc = race_confidence_score(race, scores, probs)
        assert rc.level in ["High", "Medium"]  # Dominant = high/medium
        assert rc.top_probability > 0.5

    def test_empty_field_low_confidence(self):
        race = make_race(horses=[])
        rc = race_confidence_score(race, {}, {})
        assert rc.level == "Low"

    def test_confidence_fields_are_floats(self):
        race = make_race()
        scores = {"h1": 80, "h2": 75, "h3": 70}
        probs = softmax_probabilities(scores)
        rc = race_confidence_score(race, scores, probs)
        for field in [rc.top_probability, rc.probability_gap, rc.data_quality, rc.pace_stability, rc.field_competitiveness]:
            assert isinstance(field, float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
