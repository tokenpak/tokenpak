"""
TrackEdge Feature Engine - Handicapping features for horse racing.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class PaceStyle(Enum):
    EARLY = "E"
    EARLY_PACE = "EP"
    PACE = "P"
    STRETCH = "S"


class ClassFitFlag(Enum):
    PURSE_DROP = "purse_drop"
    CLAIM_DROP = "claim_drop"
    CLASS_RAISE = "class_raise"
    NEUTRAL = "neutral"


@dataclass
class SpeedRating:
    score: float
    trend: str
    last_three: List[float]


@dataclass
class ClassFitResult:
    score: float
    flags: List[str]
    class_rating_avg: float
    todays_class: float


@dataclass
class WorkoutFitness:
    score: float
    recent_work_count: int
    days_since_last: int
    has_bullet: bool


def apply_shrinkage(stat: float, starts: int, baseline: float = 0.12, k: float = 7.0) -> float:
    """Apply shrinkage: (starts * stat + baseline * k) / (starts + k)"""
    if starts == 0:
        return baseline
    return (starts * stat + baseline * k) / (starts + k)


def speed_score(horse: Dict) -> SpeedRating:
    """Speed rating: 0.5*last + 0.3*2nd + 0.2*3rd. Detect trend."""
    ratings = horse.get("speed_ratings", [0, 0, 0])[:3]
    while len(ratings) < 3:
        ratings.append(0)
    
    weighted = 0.5 * ratings[0] + 0.3 * ratings[1] + 0.2 * ratings[2]
    
    if len(ratings) >= 3 and ratings[0] > ratings[1] > ratings[2]:
        trend = "improving"
    elif len(ratings) >= 3 and ratings[0] < ratings[1] < ratings[2]:
        trend = "declining"
    else:
        trend = "stable"
    
    return SpeedRating(score=float(weighted), trend=trend, last_three=ratings)


def pace_style(horse: Dict) -> str:
    """Classify pace: E (early), EP, P (pace), S (stretch)."""
    avg_pace = horse.get("avg_pace", 3.0)
    avg_lenback = horse.get("avg_lenback", 5.0)
    
    if avg_pace < 1.5 or avg_lenback < 1.0:
        return "E"
    if 1.5 <= avg_pace <= 3.0 and avg_lenback < 4.0:
        return "EP"
    if 2.0 <= avg_pace <= 4.0:
        return "P"
    return "S"


def race_pace_scenario(race: Dict) -> Dict[str, float]:
    """Count early horses → classify pace (slow/honest/fast). Return adjustments."""
    horses = race.get("horses", [])
    early_count = sum(1 for h in horses if h.get("pace_style") == "E")
    
    adjustments = {}
    if early_count == 0:
        for h in horses:
            style = h.get("pace_style", "S")
            adjustments[h.get("id")] = 1.05 if style in ["P", "S"] else 1.0
    elif early_count <= 2:
        for h in horses:
            adjustments[h.get("id")] = 1.0
    else:
        for h in horses:
            style = h.get("pace_style", "S")
            if style == "E":
                adjustments[h.get("id")] = 0.95
            elif style == "S":
                adjustments[h.get("id")] = 1.10
            else:
                adjustments[h.get("id")] = 1.0
    
    return adjustments


def class_fit(horse: Dict, race: Dict) -> ClassFitResult:
    """Compare horse class rating to today's race. Return score + flags."""
    horse_avg_class = horse.get("avg_class_rating", 50000)
    race_class = race.get("class_rating", 50000)
    race_type = race.get("type", "Normal")
    
    class_fit_ratio = horse_avg_class / race_class if race_class > 0 else 1.0
    score = float(max(0, min(100, class_fit_ratio * 100)))
    
    flags = []
    if race_class < horse_avg_class * 0.95:
        flags.append("purse_drop")
    if race_type == "Claim" and race_class < horse_avg_class * 0.90:
        flags.append("claim_drop")
    if race_class > horse_avg_class * 1.10:
        flags.append("class_raise")
    if not flags:
        flags.append("neutral")
    
    return ClassFitResult(
        score=score,
        flags=flags,
        class_rating_avg=horse_avg_class,
        todays_class=race_class,
    )


def workout_fitness(horse: Dict) -> WorkoutFitness:
    """Compute fitness from recent workouts. Return score + bullet flag."""
    workouts = horse.get("recent_workouts", [])
    
    if not workouts:
        return WorkoutFitness(score=50.0, recent_work_count=0, days_since_last=365, has_bullet=False)
    
    recent_work_count = len([w for w in workouts if w.get("days_ago", 999) <= 30])
    days_since_last = workouts[0].get("days_ago", 365)
    has_bullet = any(w.get("days_ago", 999) <= 14 and w.get("rank", 999) <= 5 for w in workouts)
    
    if days_since_last <= 7:
        base_score = 90
    elif days_since_last <= 14:
        base_score = 80
    elif days_since_last <= 21:
        base_score = 70
    else:
        base_score = max(30, 100 - days_since_last)
    
    if recent_work_count >= 2:
        base_score = min(100, base_score + 10)
    if has_bullet:
        base_score = min(100, base_score + 15)
    
    return WorkoutFitness(
        score=base_score,
        recent_work_count=recent_work_count,
        days_since_last=days_since_last,
        has_bullet=has_bullet,
    )


def layoff_penalty(horse: Dict) -> float:
    """Layoff penalty: days_since_race → mild/major. Unless strong workouts."""
    days_off = horse.get("days_since_last_race", 0)
    workouts = horse.get("recent_workouts", [])
    recent_bullets = sum(1 for w in workouts if w.get("days_ago", 999) <= 30 and w.get("rank", 999) <= 5)
    
    if days_off < 30:
        return 1.0
    elif days_off < 60:
        return 1.0 if recent_bullets > 0 else 0.95
    elif days_off < 180:
        return 1.0 if recent_bullets >= 2 else 0.85
    else:
        return 0.70 if recent_bullets >= 2 else 0.50


def connections_score(horse: Dict) -> float:
    """Jockey + trainer win rates. Apply shrinkage for low sample size."""
    jockey_wr = horse.get("jockey_win_rate", 0.15)
    trainer_wr = horse.get("trainer_win_rate", 0.18)
    jockey_starts = horse.get("jockey_starts", 0)
    trainer_starts = horse.get("trainer_starts", 0)
    
    jockey_adj = apply_shrinkage(jockey_wr, jockey_starts, baseline=0.15, k=10)
    trainer_adj = apply_shrinkage(trainer_wr, trainer_starts, baseline=0.18, k=15)
    
    combined_wr = 0.4 * jockey_adj + 0.6 * trainer_adj
    score = min(100, max(0, combined_wr * 250))
    
    return score


def first_time_starter_reweight(horse: Dict) -> Dict[str, float]:
    """Reweight features for first-time starters: 60% workout, 30% trainer, 10% sire."""
    starts = horse.get("starts", 0)
    
    if starts >= 2:
        return {"speed_score": 0.35, "class_fit": 0.20, "pace_fit": 0.15, "form_fitness": 0.20, "connections": 0.10}
    elif starts == 1:
        return {"speed_score": 0.20, "class_fit": 0.10, "pace_fit": 0.10, "form_fitness": 0.40, "connections": 0.20}
    else:
        return {"speed_score": 0.10, "class_fit": 0.10, "pace_fit": 0.10, "form_fitness": 0.60, "connections": 0.10}


def filter_comparable_races(past_performances: list, race: dict) -> list:
    """
    Filter past performances to races comparable to today's race.
    
    Criteria: similar distance, class, surface
    """
    if not past_performances:
        return []
    
    target_distance = race.get("distance", 0)
    target_class = race.get("class_rating", 50000)
    target_surface = race.get("surface", "dirt")
    
    comparable = []
    for pp in past_performances:
        distance = pp.get("distance", 0)
        class_rating = pp.get("class_rating", 50000)
        surface = pp.get("surface", "dirt")
        
        # Within 0.5 furlongs, class within 20%, same surface
        if (abs(distance - target_distance) < 0.5 and
            abs(class_rating - target_class) < target_class * 0.2 and
            surface == target_surface):
            comparable.append(pp)
    
    return comparable


def calculate_pace_metrics(horse: dict, race: dict) -> dict:
    """
    Calculate pace-related metrics from comparable past performances.
    """
    pps = horse.get("past_performances", [])
    comparable = filter_comparable_races(pps, race)
    
    if len(comparable) < 1:
        return {
            "avg_pacefigure": 0,
            "avg_lenback1": 0,
            "avg_position1": 0,
            "avg_position2": 0,
        }
    
    # Average last 3 comparable races
    pace_figs = [p.get("pacefigure", 0) for p in comparable[:3]]
    lenbacks = [p.get("lenback1", 0) for p in comparable[:3]]
    pos1s = [p.get("position1", 0) for p in comparable[:3]]
    pos2s = [p.get("position2", 0) for p in comparable[:3]]
    
    return {
        "avg_pacefigure": sum(pace_figs) / len(pace_figs) if pace_figs else 0,
        "avg_lenback1": sum(lenbacks) / len(lenbacks) if lenbacks else 0,
        "avg_position1": sum(pos1s) / len(pos1s) if pos1s else 0,
        "avg_position2": sum(pos2s) / len(pos2s) if pos2s else 0,
    }


def classify_pace_style_improved(avg_lenback1: float) -> str:
    """Classify horse's running style based on lengths back at first call."""
    if avg_lenback1 <= 1.5:
        return "E"
    elif avg_lenback1 <= 4.0:
        return "EP"
    elif avg_lenback1 <= 7.0:
        return "P"
    else:
        return "S"


def race_pace_projection(race: dict) -> dict:
    """
    Analyze field's pace characteristics using top entrants.
    """
    horses = race.get("horses", [])
    
    # Get top 4 by pace figure
    by_pace = sorted(
        horses,
        key=lambda h: h.get("pace_metrics", {}).get("avg_pacefigure", 0),
        reverse=True
    )[:4]
    
    if not by_pace:
        return {"race_pace_index": 90, "pace_label": "Honest"}
    
    race_pace_index = sum(
        h.get("pace_metrics", {}).get("avg_pacefigure", 0) for h in by_pace
    ) / len(by_pace)
    
    # Classify
    if race_pace_index < 85:
        pace_label = "Slow"
    elif race_pace_index < 95:
        pace_label = "Honest"
    elif race_pace_index < 105:
        pace_label = "Fast"
    else:
        pace_label = "Meltdown"
    
    return {
        "race_pace_index": race_pace_index,
        "pace_label": pace_label,
    }


def pace_fit_adjustment(horse: dict, race_pace_label: str) -> float:
    """
    Adjust horse score based on pace fit.
    
    Returns: adjustment to add to power_score (capped -5 to +5)
    """
    style = horse.get("pace_style", "U")
    adjustment = 0
    
    if race_pace_label == "Slow":
        if style == "E":
            adjustment = 2.0
        elif style == "S":
            adjustment = -1.0
    
    elif race_pace_label == "Honest":
        adjustment = 0
    
    elif race_pace_label == "Fast":
        if style in ["EP", "P"]:
            adjustment = 2.0
        elif style == "E":
            adjustment = -1.0
    
    elif race_pace_label == "Meltdown":
        if style in ["P", "S"]:
            adjustment = 3.0
        elif style == "E":
            adjustment = -2.0
    
    return max(-5, min(5, adjustment))


def speed_score_field_relative(horse: dict, race: dict) -> float:
    """
    Score horse's speed relative to today's field (z-score based).
    
    Prevents weak fields from producing artificial superhorses.
    Returns score bounded 20-90.
    """
    pps = horse.get("past_performances", [])
    comparable = filter_comparable_races(pps, race)[:3]
    
    speeds = [p.get("speedfigur", 0) for p in comparable]
    if not speeds:
        return 50
    
    # Weighted average of last 3
    weighted_speed = (
        0.5 * (speeds[0] if len(speeds) > 0 else 0) +
        0.3 * (speeds[1] if len(speeds) > 1 else 0) +
        0.2 * (speeds[2] if len(speeds) > 2 else 0)
    )
    
    # Get field speeds
    all_horse_speeds = []
    for h in race.get("horses", []):
        h_pps = h.get("past_performances", [])
        h_comparable = filter_comparable_races(h_pps, race)[:3]
        h_speeds = [p.get("speedfigur", 0) for p in h_comparable]
        if h_speeds:
            h_weighted = (
                0.5 * h_speeds[0] +
                0.3 * (h_speeds[1] if len(h_speeds) > 1 else 0) +
                0.2 * (h_speeds[2] if len(h_speeds) > 2 else 0)
            )
            all_horse_speeds.append(h_weighted)
    
    if not all_horse_speeds or len(all_horse_speeds) < 2:
        return 50
    
    field_mean = sum(all_horse_speeds) / len(all_horse_speeds)
    field_std = (
        sum((s - field_mean) ** 2 for s in all_horse_speeds) / len(all_horse_speeds)
    ) ** 0.5
    
    # Z-score and convert to 20-90 scale
    if field_std == 0:
        z = 0
    else:
        z = (weighted_speed - field_mean) / field_std
    
    speed_score = 50 + (z * 15)
    return max(20, min(90, speed_score))



def engineer_features(horse: Dict, race: Dict) -> Dict:
    """
    Aggregate all feature engineering for a single horse in a race.
    Returns a flat dict of computed features for use by the scoring engine.
    This is the primary entry point for feature engineering.
    """
    speed = speed_score(horse)
    pace = pace_style(horse)
    race_pace = race_pace_scenario(race)
    cf = class_fit(horse, race)
    wf = workout_fitness(horse)
    lp = layoff_penalty(horse)
    conn = connections_score(horse)
    pace_label = race_pace.get("label", "P")
    pace_fit = pace_fit_adjustment(horse, pace_label)

    return {
        "speed_score": speed.score if hasattr(speed, "score") else 50,
        "speed_trend": speed.trend if hasattr(speed, "trend") else "flat",
        "pace_style": pace,
        "race_pace_scenario": race_pace,
        "class_fit": cf.score if hasattr(cf, "score") else 50,
        "class_flags": cf.flags if hasattr(cf, "flags") else [],
        "workout_fitness": wf.score if hasattr(wf, "score") else 50,
        "layoff_penalty": lp,
        "connections_score": conn,
        "pace_fit": pace_fit,
        "form_fitness": max(0, (wf.score if hasattr(wf, "score") else 50) - lp),
    }
