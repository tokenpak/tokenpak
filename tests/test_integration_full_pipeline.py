"""
test_integration_full_pipeline.py — End-to-End Pipeline Integration Tests

Tests the full TrackEdge pipeline using the available modules:
  - feature_engine (✅ available)
  - scoring_engine (✅ available)
  - probability (✅ available)
  - xml_parser (❌ not yet implemented — those tests skip)
  - edge_engine (❌ not yet implemented — those tests skip)

Tests here exercise what IS implemented end-to-end.
"""

import math
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trackedge.processing.feature_engine import (
    speed_score, pace_style, race_pace_scenario, class_fit,
    workout_fitness, layoff_penalty, connections_score, first_time_starter_reweight,
)
from trackedge.model.scoring_engine import (
    power_score, race_confidence_score, softmax_probabilities as scoring_softmax,
)
from trackedge.model.probability import (
    softmax_probabilities as prob_softmax, top_contenders,
)


# ---------------------------------------------------------------------------
# Synthetic Data Factory
# ---------------------------------------------------------------------------

def make_horse(prog=1, name="TestHorse", speed_ratings=None, pace_sty="EP",
               morn_odds="5-1", jwin=0.18, twin=0.20, jstarts=50, tstarts=100,
               avg_class=50000, days_since=14, workouts=None, starts=10):
    if speed_ratings is None:
        speed_ratings = [85, 80, 75]
    if workouts is None:
        workouts = [{"days_ago": 7, "rank": 3}]
    return {
        "id": f"h{prog}",
        "program": prog,
        "name": name,
        "morn_odds": morn_odds,
        "speed_ratings": speed_ratings,
        "pace_style": pace_sty,
        "avg_pace": 2.5,
        "avg_lenback": 3.0,
        "days_since_last_race": days_since,
        "recent_workouts": workouts,
        "jockey_win_rate": jwin,
        "trainer_win_rate": twin,
        "jockey_starts": jstarts,
        "trainer_starts": tstarts,
        "starts": starts,
        "avg_class_rating": avg_class,
        "past_performances": [],
    }


def make_race(number=1, horses=None, class_rating=60000, surface="D", purse=60000, type_="Normal"):
    if horses is None:
        horses = [
            make_horse(i, f"Horse{i}",
                       speed_ratings=[90 - i*5, 85 - i*5, 80 - i*5],
                       pace_sty=["E", "EP", "P", "S"][i % 4],
                       morn_odds=f"{i*2}-1")
            for i in range(1, 6)
        ]
    return {
        "number": number,
        "track": "SA",
        "date": "2026-02-27",
        "surface": surface,
        "purse": purse,
        "class_rating": class_rating,
        "type": type_,
        "horses": horses,
    }


def make_9_race_card():
    """Generate the full 9-race card."""
    return [make_race(number=n, class_rating=50000 + n * 5000, purse=50000 + n * 5000)
            for n in range(1, 10)]


# ---------------------------------------------------------------------------
# Pipeline Helper: Run All Stages
# ---------------------------------------------------------------------------

def run_full_pipeline(races):
    """
    Execute the full TrackEdge pipeline on a list of races.
    Returns enriched races with probabilities and confidence scores.
    """
    enriched = []
    for race in races:
        # Stage 1: Feature Engineering
        for horse in race["horses"]:
            horse["_speed_score"] = speed_score(horse).score
            horse["_class_fit"] = class_fit(horse, race).score
            horse["_workout_fitness"] = workout_fitness(horse).score
            horse["_layoff_penalty"] = layoff_penalty(horse)
            horse["_connections"] = connections_score(horse)
            horse["_pace_style"] = pace_style(horse)

        # Stage 2: Pace Scenario
        for h in race["horses"]:
            h.setdefault("id", f"h{h.get('program', 0)}")
        pace_adj = race_pace_scenario(race)

        # Stage 3: Power Score
        horse_scores = {}
        for horse in race["horses"]:
            features = {
                "speed_score": horse["_speed_score"],
                "class_fit": horse["_class_fit"],
                "pace_fit": 60.0,  # Placeholder (no pace_fit function in current engine)
                "form_fitness": horse["_workout_fitness"],
                "connections_score": horse["_connections"],
            }
            ps = power_score(horse, race, features)
            # Apply pace adjustment (from race_pace_scenario)
            adj = pace_adj.get(horse["id"], 1.0)
            horse["power_score"] = ps * adj
            horse_scores[horse["id"]] = horse["power_score"]

        # Stage 4: Probabilities (using probability module)
        probs = prob_softmax(horse_scores)
        for horse in race["horses"]:
            horse["win_probability"] = probs.get(horse["id"], 0.0)

        # Stage 5: Race Confidence (using scoring module)
        confidence = race_confidence_score(race, horse_scores, probs)
        race["confidence"] = confidence

        # Stage 6: Top Contenders
        race["top_contenders"] = top_contenders(probs, n=3)

        enriched.append(race)
    return enriched


# ---------------------------------------------------------------------------
# 1. Pipeline Completes Without Errors
# ---------------------------------------------------------------------------

class TestPipelineCompletion:

    def test_full_pipeline_9_races_no_errors(self):
        """Full pipeline must complete without raising any exception."""
        races = make_9_race_card()
        enriched = run_full_pipeline(races)
        assert len(enriched) == 9

    def test_all_races_have_horses(self):
        """All 9 enriched races must have at least 1 horse."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            assert len(race["horses"]) > 0, f"Race {race['number']} has no horses"

    def test_all_races_have_confidence(self):
        """All 9 races must have a confidence score."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            assert race.get("confidence") is not None, (
                f"Race {race['number']} missing confidence"
            )
            assert race["confidence"].level in ["High", "Medium", "Low"]

    def test_all_races_have_top_contenders(self):
        """All 9 races must produce top contenders."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            assert race.get("top_contenders"), f"Race {race['number']} has no top_contenders"
            assert len(race["top_contenders"]) <= 3


# ---------------------------------------------------------------------------
# 2. No NaN/None in Final Output
# ---------------------------------------------------------------------------

class TestNoNullsInOutput:

    def test_no_nan_power_scores(self):
        """All power_scores in 9-race output must be finite."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                ps = horse.get("power_score")
                assert ps is not None, f"None power_score for {horse['name']}"
                assert not math.isnan(ps), f"NaN power_score for {horse['name']}"
                assert not math.isinf(ps), f"Inf power_score for {horse['name']}"

    def test_no_nan_probabilities(self):
        """All win_probabilities must be finite non-NaN floats."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                wp = horse.get("win_probability")
                assert wp is not None, f"None win_probability for {horse['name']}"
                assert not math.isnan(wp), f"NaN win_probability for {horse['name']}"

    def test_no_none_race_number(self):
        """All races must have a non-None number."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            assert race.get("number") is not None

    def test_no_none_horse_names(self):
        """All horses must have a non-None name."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                assert horse.get("name") is not None


# ---------------------------------------------------------------------------
# 3. Probability Correctness in Full Pipeline
# ---------------------------------------------------------------------------

class TestPipelineProbabilities:

    def test_probabilities_sum_to_1_each_race(self):
        """Per-race win_probability sum must equal 1.0."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            probs = [h["win_probability"] for h in race["horses"]]
            total = sum(probs)
            assert abs(total - 1.0) < 1e-9, (
                f"Race {race['number']}: probs sum to {total}"
            )

    def test_no_probabilities_exceed_80_percent(self):
        """No horse should get >80% win probability (guardrail check)."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                wp = horse["win_probability"]
                assert wp <= 0.80, (
                    f"Race {race['number']}: {horse['name']} has {wp:.1%} probability"
                )

    def test_favorite_has_highest_probability(self):
        """The horse with the highest power score must have the highest probability."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            horses = race["horses"]
            best_horse = max(horses, key=lambda h: h["power_score"])
            best_prob = best_horse["win_probability"]
            for horse in horses:
                if horse["id"] != best_horse["id"]:
                    assert best_prob >= horse["win_probability"], (
                        f"Race {race['number']}: top scorer {best_horse['name']} "
                        f"({best_prob:.1%}) not higher than {horse['name']} "
                        f"({horse['win_probability']:.1%})"
                    )

    def test_probabilities_all_between_0_and_1(self):
        """All probabilities must be in [0, 1]."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                wp = horse["win_probability"]
                assert 0 <= wp <= 1, (
                    f"Race {race['number']}: {horse['name']} probability {wp} out of range"
                )


# ---------------------------------------------------------------------------
# 4. Power Score Correctness
# ---------------------------------------------------------------------------

class TestPipelinePowerScores:

    def test_power_scores_in_0_100_range(self):
        """Power scores should be within expected 0-100 range (after pace adj, may be slightly over)."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            for horse in race["horses"]:
                ps = horse["power_score"]
                # Pace adjustments can push slightly over 100 (max 1.10 * 100 = 110)
                assert 0 <= ps <= 110, (
                    f"Race {race['number']}: {horse['name']} power score {ps} out of range"
                )

    def test_better_speed_horse_higher_score(self):
        """Horse with 90-rated speed should outscore horse with 50-rated speed."""
        race = make_race(horses=[
            make_horse(1, "Fast", speed_ratings=[90, 88, 85]),
            make_horse(2, "Slow", speed_ratings=[50, 48, 45]),
        ])
        enriched = run_full_pipeline([race])
        fast = next(h for h in enriched[0]["horses"] if h["name"] == "Fast")
        slow = next(h for h in enriched[0]["horses"] if h["name"] == "Slow")
        assert fast["power_score"] > slow["power_score"]


# ---------------------------------------------------------------------------
# 5. Race Confidence Score
# ---------------------------------------------------------------------------

class TestPipelineConfidenceScores:

    def test_confidence_levels_are_valid(self):
        """All confidence levels must be High/Medium/Low."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            assert race["confidence"].level in ["High", "Medium", "Low"]

    def test_confidence_top_probability_is_max_prob(self):
        """Confidence.top_probability should match the maximum win_probability in the race."""
        races = run_full_pipeline(make_9_race_card())
        for race in races:
            max_prob = max(h["win_probability"] for h in race["horses"])
            conf_top = race["confidence"].top_probability
            assert abs(conf_top - max_prob) < 1e-9, (
                f"Race {race['number']}: confidence.top_probability {conf_top} "
                f"!= actual max {max_prob}"
            )

    def test_dominant_favorite_high_or_medium_confidence(self):
        """A race with a very dominant horse should produce High or Medium confidence."""
        race = make_race(horses=[
            make_horse(1, "Dominant", speed_ratings=[99, 97, 95]),
            make_horse(2, "Also-Ran", speed_ratings=[50, 48, 45]),
            make_horse(3, "Outsider", speed_ratings=[40, 38, 35]),
        ])
        enriched = run_full_pipeline([race])
        assert enriched[0]["confidence"].level in ["High", "Medium"]


# ---------------------------------------------------------------------------
# 6. Pipeline Resilience — Edge Cases
# ---------------------------------------------------------------------------

class TestPipelineResilience:

    def test_first_time_starters_no_crash(self):
        """First-time starters (0 past_performances, 0 starts) must not crash pipeline."""
        horse = make_horse(1, "Debut", speed_ratings=[], starts=0,
                           workouts=[{"days_ago": 5, "rank": 1}])
        horse["past_performances"] = []
        race = make_race(horses=[horse, make_horse(2, "Veteran")])
        enriched = run_full_pipeline([race])
        debut = next(h for h in enriched[0]["horses"] if h["name"] == "Debut")
        assert debut["power_score"] is not None
        assert not math.isnan(debut["win_probability"])

    def test_single_horse_race_all_probability(self):
        """Single-horse race must give 100% win probability."""
        race = make_race(horses=[make_horse(1, "Solo")])
        enriched = run_full_pipeline([race])
        solo = enriched[0]["horses"][0]
        assert abs(solo["win_probability"] - 1.0) < 1e-9

    def test_large_field_8_horses_no_errors(self):
        """8-horse race must complete without errors."""
        horses = [make_horse(i, f"Horse{i}", speed_ratings=[80 - i*3, 75 - i*3, 70 - i*3])
                  for i in range(1, 9)]
        race = make_race(horses=horses)
        enriched = run_full_pipeline([race])
        assert len(enriched[0]["horses"]) == 8
        probs = [h["win_probability"] for h in enriched[0]["horses"]]
        assert abs(sum(probs) - 1.0) < 1e-9

    def test_extreme_layoff_horse_handled(self):
        """Horse with 365-day layoff must not crash pipeline."""
        horse = make_horse(1, "Rusty", days_since=365, workouts=[])
        race = make_race(horses=[horse, make_horse(2, "Fresh")])
        enriched = run_full_pipeline([race])
        rusty = next(h for h in enriched[0]["horses"] if h["name"] == "Rusty")
        assert not math.isnan(rusty["win_probability"])

    def test_missing_odds_no_crash(self):
        """Missing/empty morn_odds must not crash the pipeline."""
        horse = make_horse(1, "NoOdds", morn_odds="")
        race = make_race(horses=[horse, make_horse(2, "Normal")])
        # Pipeline should complete without crash (odds are used by edge_engine, not here)
        enriched = run_full_pipeline([race])
        assert len(enriched[0]["horses"]) == 2


# ---------------------------------------------------------------------------
# 7. Missing Module Status Report
# ---------------------------------------------------------------------------

class TestMissingModuleStatus:
    """Document which modules are available and which are blocked."""

    def test_feature_engine_available(self):
        """trackedge.processing.feature_engine must be importable."""
        from trackedge.processing.feature_engine import speed_score  # noqa
        assert callable(speed_score)

    def test_scoring_engine_available(self):
        """trackedge.model.scoring_engine must be importable."""
        from trackedge.model.scoring_engine import power_score  # noqa
        assert callable(power_score)

    def test_probability_engine_available(self):
        """trackedge.model.probability must be importable."""
        from trackedge.model.probability import softmax_probabilities  # noqa
        assert callable(softmax_probabilities)

    def test_xml_parser_status(self):
        """Document xml_parser availability."""
        try:
            from trackedge.parser.xml_parser import parse_xml  # noqa
            assert True, "xml_parser is now available"
        except ImportError:
            pytest.skip(
                "BLOCKED: trackedge/parser/xml_parser.py not implemented\n"
                "Impact: Cannot validate 9-race XML parsing (sa20260227ppsXML.xml)"
            )

    def test_edge_engine_status(self):
        """Document edge_engine availability."""
        try:
            from trackedge.model.edge_engine import edge_calculation  # noqa
            assert True, "edge_engine is now available"
        except ImportError:
            pytest.skip(
                "BLOCKED: trackedge/model/edge_engine.py not implemented\n"
                "Impact: Cannot validate edge calculation or bankroll allocation"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
