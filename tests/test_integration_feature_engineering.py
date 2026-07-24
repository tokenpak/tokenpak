"""
test_feature_engineering.py — Feature Engineering Integration Tests

Tests the full feature_engine.py module against synthetic 9-race data.
These should all PASS with the current implementation.
"""

import math

import pytest

# trackedge is a separate project not installed in the slim release test env;
# skip cleanly so the release auto-publish gate doesn't error on collection.
pytest.importorskip(
    "trackedge.processing.feature_engine",
    reason="trackedge is a separate project not installed in slim test env",
)

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trackedge.processing.feature_engine import (
    apply_shrinkage,
    class_fit,
    connections_score,
    first_time_starter_reweight,
    layoff_penalty,
    pace_style,
    race_pace_scenario,
    speed_score,
    workout_fitness,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_horse(**overrides):
    defaults = {
        "program": 1,
        "name": "TestHorse",
        "speed_ratings": [85, 80, 75],
        "avg_pace": 2.5,
        "avg_lenback": 3.0,
        "pace_style": "EP",
        "avg_class_rating": 50000,
        "days_since_last_race": 14,
        "recent_workouts": [{"days_ago": 7, "rank": 3}],
        "jockey_win_rate": 0.18,
        "trainer_win_rate": 0.20,
        "jockey_starts": 50,
        "trainer_starts": 100,
        "starts": 10,
        "past_performances": [
            {
                "racedate": "2026-01-15",
                "surface": "D",
                "speedfigur": 85,
                "lenback1": 2.0,
                "lenback2": 3.5,
                "position1": 2,
                "position2": 3,
                "pacefigure": 92,
                "purse": 50000,
            },
            {
                "racedate": "2025-12-20",
                "surface": "D",
                "speedfigur": 80,
                "lenback1": 2.5,
                "lenback2": 4.0,
                "position1": 3,
                "position2": 4,
                "pacefigure": 88,
                "purse": 48000,
            },
        ],
    }
    defaults.update(overrides)
    return defaults


def make_race(**overrides):
    defaults = {
        "number": 1,
        "track": "SA",
        "surface": "D",
        "purse": 60000,
        "class_rating": 60000,
        "type": "Normal",
        "horses": [
            {"id": "h1", "pace_style": "E", "speed_ratings": [90], "past_performances": []},
            {"id": "h2", "pace_style": "P", "speed_ratings": [80], "past_performances": []},
            {"id": "h3", "pace_style": "S", "speed_ratings": [75], "past_performances": []},
        ],
    }
    defaults.update(overrides)
    return defaults


def synthetic_9_races():
    """Generate 9 synthetic races for integration testing."""
    races = []
    for n in range(1, 10):
        horses = [
            make_horse(
                program=i,
                name=f"Horse{i}",
                speed_ratings=[90 - i * 3, 85 - i * 3, 80 - i * 3],
                pace_style=["E", "EP", "P", "S"][i % 4],
            )
            for i in range(1, 6)
        ]
        races.append(
            {
                "number": n,
                "track": "SA",
                "date": "2026-02-27",
                "surface": "D",
                "purse": 50000 + n * 5000,
                "class_rating": 50000 + n * 5000,
                "type": "Normal",
                "horses": horses,
            }
        )
    return races


# ---------------------------------------------------------------------------
# 1. Speed Score — across 9-race synthetic field
# ---------------------------------------------------------------------------


class TestSpeedScoreIntegration:
    def test_no_nan_speed_scores(self):
        """Speed scores must be finite floats for all horses in 9-race field."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                result = speed_score(horse)
                assert isinstance(result.score, float), "speed_score.score must be float"
                assert not math.isnan(result.score), f"NaN speed score for {horse['name']}"
                assert not math.isinf(result.score), f"Inf speed score for {horse['name']}"

    def test_speed_score_range(self):
        """Weighted average of 0-100 ratings should stay in 0-100."""
        for ratings in ([90, 85, 80], [0, 0, 0], [100, 100, 100], [50]):
            horse = make_horse(speed_ratings=ratings)
            result = speed_score(horse)
            assert 0 <= result.score <= 100, f"Score {result.score} out of range for {ratings}"

    def test_speed_score_trend_coverage(self):
        """All three trend values must be produceable."""
        trends_seen = set()
        for ratings in ([90, 80, 70], [70, 80, 90], [80, 80, 80]):
            result = speed_score(make_horse(speed_ratings=ratings))
            trends_seen.add(result.trend)
        assert "improving" in trends_seen
        assert "declining" in trends_seen
        assert "stable" in trends_seen

    def test_speed_score_empty_ratings(self):
        """Empty speed_ratings must not crash — returns 0.0."""
        result = speed_score(make_horse(speed_ratings=[]))
        assert result.score == 0.0

    def test_speed_score_partial_ratings(self):
        """Single rating: 0.5 * rating."""
        result = speed_score(make_horse(speed_ratings=[80]))
        assert math.isclose(result.score, 40.0, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# 2. Pace Style — classification
# ---------------------------------------------------------------------------


class TestPaceStyleIntegration:
    def test_pace_style_values_are_valid(self):
        """All horses in 9-race field must have a valid pace style."""
        valid = {"E", "EP", "P", "S"}
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                style = pace_style(horse)
                assert style in valid, f"Invalid pace style {style!r} for {horse['name']}"

    def test_pace_style_early(self):
        assert pace_style(make_horse(avg_pace=0.5, avg_lenback=0.5)) == "E"

    def test_pace_style_early_pace(self):
        assert pace_style(make_horse(avg_pace=2.0, avg_lenback=3.0)) == "EP"

    def test_pace_style_stretch(self):
        assert pace_style(make_horse(avg_pace=6.0, avg_lenback=10.0)) == "S"


# ---------------------------------------------------------------------------
# 3. Race Pace Scenario — adjustments
# ---------------------------------------------------------------------------


class TestRacePaceScenarioIntegration:
    def test_adjustments_are_positive_floats(self):
        """All pace scenario adjustments must be > 0."""
        races = synthetic_9_races()
        for race in races:
            # race_pace_scenario uses 'pace_style' field on horses
            for h in race["horses"]:
                h.setdefault("id", h.get("name", str(h["program"])))
            adjustments = race_pace_scenario(race)
            for horse_id, adj in adjustments.items():
                assert isinstance(adj, float), f"Adjustment must be float, got {type(adj)}"
                assert adj > 0, f"Non-positive adjustment {adj} for {horse_id}"

    def test_slow_pace_boosts_stretch_runners(self):
        """No early horses → P/S get 1.05 boost."""
        race = make_race(
            horses=[
                {"id": "h1", "pace_style": "P", "speed_ratings": [80], "past_performances": []},
                {"id": "h2", "pace_style": "S", "speed_ratings": [75], "past_performances": []},
            ]
        )
        adj = race_pace_scenario(race)
        assert adj["h1"] == pytest.approx(1.05)
        assert adj["h2"] == pytest.approx(1.05)

    def test_fast_pace_penalizes_early_horses(self):
        """3+ early horses → E gets penalty, S gets boost."""
        race = make_race(
            horses=[
                {"id": "e1", "pace_style": "E", "speed_ratings": [90], "past_performances": []},
                {"id": "e2", "pace_style": "E", "speed_ratings": [85], "past_performances": []},
                {"id": "e3", "pace_style": "E", "speed_ratings": [80], "past_performances": []},
                {"id": "s1", "pace_style": "S", "speed_ratings": [75], "past_performances": []},
            ]
        )
        adj = race_pace_scenario(race)
        assert adj["e1"] < 1.0
        assert adj["s1"] > 1.0


# ---------------------------------------------------------------------------
# 4. Class Fit — scoring
# ---------------------------------------------------------------------------


class TestClassFitIntegration:
    def test_class_fit_scores_in_range(self):
        """Class fit score must be in 0-100 for all 9-race synthetic field."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                result = class_fit(horse, race)
                assert 0 <= result.score <= 100, (
                    f"Class fit {result.score} out of range for {horse['name']}"
                )

    def test_no_nan_class_fit(self):
        """Class fit must never produce NaN."""
        for class_rat in [0, 10000, 50000, 100000]:
            race = make_race(class_rating=class_rat)
            horse = make_horse(avg_class_rating=50000)
            result = class_fit(horse, race)
            assert not math.isnan(result.score), f"NaN class fit for class_rating={class_rat}"

    def test_purse_drop_flag(self):
        horse = make_horse(avg_class_rating=60000)
        race = make_race(class_rating=48000)
        result = class_fit(horse, race)
        assert "purse_drop" in result.flags

    def test_class_raise_flag(self):
        horse = make_horse(avg_class_rating=40000)
        race = make_race(class_rating=65000)
        result = class_fit(horse, race)
        assert "class_raise" in result.flags


# ---------------------------------------------------------------------------
# 5. Workout Fitness — scoring
# ---------------------------------------------------------------------------


class TestWorkoutFitnessIntegration:
    def test_workout_fitness_in_range(self):
        """Workout fitness must be 0-100 for all combinations."""
        for workouts in (
            [],
            [{"days_ago": 5, "rank": 1}],
            [{"days_ago": 90, "rank": 10}],
            [{"days_ago": 7, "rank": 3}, {"days_ago": 14, "rank": 2}],
        ):
            result = workout_fitness(make_horse(recent_workouts=workouts))
            assert 0 <= result.score <= 100, f"Out of range score {result.score}"

    def test_no_workouts_returns_50(self):
        result = workout_fitness(make_horse(recent_workouts=[]))
        assert result.score == 50.0

    def test_bullet_workout_detected(self):
        horse = make_horse(recent_workouts=[{"days_ago": 3, "rank": 1}])
        result = workout_fitness(horse)
        assert result.has_bullet is True

    def test_no_nan_workout_fitness(self):
        """Never produce NaN."""
        for days in [0, 5, 30, 90, 365]:
            horse = make_horse(recent_workouts=[{"days_ago": days, "rank": 3}])
            result = workout_fitness(horse)
            assert not math.isnan(result.score)


# ---------------------------------------------------------------------------
# 6. Layoff Penalty — penalty multiplier
# ---------------------------------------------------------------------------


class TestLayoffPenaltyIntegration:
    def test_recent_race_no_penalty(self):
        assert layoff_penalty(make_horse(days_since_last_race=10, recent_workouts=[])) == 1.0

    def test_mild_layoff_penalty(self):
        penalty = layoff_penalty(make_horse(days_since_last_race=45, recent_workouts=[]))
        assert penalty == pytest.approx(0.95)

    def test_major_layoff_penalty(self):
        penalty = layoff_penalty(make_horse(days_since_last_race=200, recent_workouts=[]))
        assert penalty == pytest.approx(0.50)

    def test_penalty_mitigated_by_workouts(self):
        horse = make_horse(
            days_since_last_race=45,
            recent_workouts=[{"days_ago": 7, "rank": 2}],
        )
        assert layoff_penalty(horse) == 1.0

    def test_all_9_races_no_negative_penalty(self):
        """Penalty must always be > 0 (multiplier, not subtractor)."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                penalty = layoff_penalty(horse)
                assert penalty > 0, f"Negative penalty {penalty} for {horse['name']}"


# ---------------------------------------------------------------------------
# 7. Connections Score — jockey/trainer quality
# ---------------------------------------------------------------------------


class TestConnectionsScoreIntegration:
    def test_connections_score_in_range(self):
        """Score must be 0-100."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                score = connections_score(horse)
                assert 0 <= score <= 100, f"Connections score {score} out of range"

    def test_no_nan_connections_score(self):
        """Never NaN."""
        for jwin, twin, jstarts, tstarts in [
            (0.0, 0.0, 0, 0),
            (0.50, 0.50, 1000, 1000),
            (0.30, 0.25, 5, 5),
        ]:
            horse = make_horse(
                jockey_win_rate=jwin,
                trainer_win_rate=twin,
                jockey_starts=jstarts,
                trainer_starts=tstarts,
            )
            score = connections_score(horse)
            assert not math.isnan(score), f"NaN for {jwin},{twin},{jstarts},{tstarts}"


# ---------------------------------------------------------------------------
# 8. First-Time Starter Reweight
# ---------------------------------------------------------------------------


class TestFirstTimeStarterReweightIntegration:
    def test_weights_sum_to_one_for_all_starts(self):
        """Weights must sum to exactly 1.0 for any starts count."""
        for starts in [0, 1, 2, 5, 10, 50]:
            horse = make_horse(starts=starts)
            weights = first_time_starter_reweight(horse)
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-9, f"Weights sum {total} != 1.0 for starts={starts}"

    def test_first_time_starter_form_fitness_dominant(self):
        """0-start horse: form_fitness (workouts) should be the biggest weight."""
        horse = make_horse(starts=0)
        weights = first_time_starter_reweight(horse)
        max_key = max(weights, key=weights.get)
        assert max_key == "form_fitness", f"Expected form_fitness dominant, got {max_key}"

    def test_experienced_horse_speed_score_dominant(self):
        """Experienced horse (5+ starts): speed_score should be biggest weight."""
        horse = make_horse(starts=10)
        weights = first_time_starter_reweight(horse)
        max_key = max(weights, key=weights.get)
        assert max_key == "speed_score", f"Expected speed_score dominant, got {max_key}"


# ---------------------------------------------------------------------------
# 9. Apply Shrinkage
# ---------------------------------------------------------------------------


class TestApplyShrinkageIntegration:
    def test_zero_starts_returns_baseline(self):
        assert apply_shrinkage(0.30, 0) == pytest.approx(0.12)

    def test_large_sample_near_stat(self):
        result = apply_shrinkage(0.25, 1000)
        assert abs(result - 0.25) < 0.01

    def test_result_in_range(self):
        """Result must always be between baseline and observed stat."""
        for stat, starts in [(0.40, 3), (0.05, 50), (0.30, 10)]:
            result = apply_shrinkage(stat, starts)
            lo, hi = min(stat, 0.12), max(stat, 0.12)
            assert lo <= result <= hi, f"Out of range: {result} for stat={stat}, starts={starts}"


# ---------------------------------------------------------------------------
# 10. No NaN/None in Full Feature Pass (9-race synthetic)
# ---------------------------------------------------------------------------


class TestNoNanNoneInFullFeaturePass:
    def _compute_all_features(self, horse, race):
        """Run all feature functions and collect results."""
        ss = speed_score(horse)
        ps = pace_style(horse)
        cf = class_fit(horse, race)
        wf = workout_fitness(horse)
        lp = layoff_penalty(horse)
        cs = connections_score(horse)
        ftw = first_time_starter_reweight(horse)
        return ss, ps, cf, wf, lp, cs, ftw

    def test_no_nan_in_any_feature(self):
        """No feature function should produce NaN for the 9-race synthetic field."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                ss, ps, cf, wf, lp, cs, ftw = self._compute_all_features(horse, race)
                name = horse["name"]
                assert not math.isnan(ss.score), f"NaN speed_score for {name}"
                assert not math.isnan(cf.score), f"NaN class_fit for {name}"
                assert not math.isnan(wf.score), f"NaN workout_fitness for {name}"
                assert not math.isnan(lp), f"NaN layoff_penalty for {name}"
                assert not math.isnan(cs), f"NaN connections_score for {name}"
                assert all(not math.isnan(v) for v in ftw.values()), f"NaN in weights for {name}"

    def test_no_none_in_any_feature(self):
        """No feature function should return None."""
        races = synthetic_9_races()
        for race in races:
            for horse in race["horses"]:
                ss, ps, cf, wf, lp, cs, ftw = self._compute_all_features(horse, race)
                name = horse["name"]
                assert ss is not None, f"None speed_score for {name}"
                assert ps is not None, f"None pace_style for {name}"
                assert cf is not None, f"None class_fit for {name}"
                assert wf is not None, f"None workout_fitness for {name}"
                assert lp is not None, f"None layoff_penalty for {name}"
                assert cs is not None, f"None connections_score for {name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
