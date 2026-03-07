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
    score = max(0, min(100, class_fit_ratio * 100))
    
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
        return {"speed_score": 0.10, "class_fit": 0.10, "pace_fit": 0.10, "form_fitness": 0.60, "connections": 0.20}
