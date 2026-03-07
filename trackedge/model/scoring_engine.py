"""
TrackEdge Scoring Engine - Power scores and race probabilities.
"""

import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class RaceConfidence:
    level: str
    top_probability: float
    probability_gap: float
    data_quality: float
    pace_stability: float
    field_competitiveness: float


def power_score(horse: Dict, race: Dict, features: Dict) -> float:
    """
    Compute 0-100 power score from features.
    
    Weights:
    - 0.35 speed_score
    - 0.20 class_fit
    - 0.15 pace_fit
    - 0.20 form_fitness
    - 0.10 connections
    """
    speed = features.get("speed_score", 50)
    class_fit = features.get("class_fit", 50)
    pace_fit = features.get("pace_fit", 50)
    form_fitness = features.get("form_fitness", 50)
    connections = features.get("connections_score", 50)
    
    score = (
        0.35 * speed +
        0.20 * class_fit +
        0.15 * pace_fit +
        0.20 * form_fitness +
        0.10 * connections
    )
    
    # Clamp to 0-100
    return max(0, min(100, score))


def softmax_probabilities(race: Dict, horse_scores: Dict[str, float], temperature: float = 15.0) -> Dict[str, float]:
    """
    Convert power scores to probabilities using softmax.
    
    p_i = exp(score_i / T) / sum(exp(score_j / T))
    
    Ensures probabilities sum to 100%.
    
    Args:
        race: Race dict
        horse_scores: Dict mapping horse_id → power_score (0-100)
        temperature: Softmax temperature (higher = flatter distribution)
    
    Returns:
        Dict mapping horse_id → win_probability (0-1)
    """
    if not horse_scores:
        return {}
    
    # Normalize scores to 0-1 range (they come in as 0-100)
    normalized = {hid: score / 100.0 for hid, score in horse_scores.items()}
    
    # Compute exp(score / T)
    exps = {hid: np.exp(score / temperature) for hid, score in normalized.items()}
    
    # Sum of exponentials
    exp_sum = sum(exps.values())
    
    # Softmax: each horse gets exp(score/T) / sum_exp
    if exp_sum == 0:
        # Edge case: all scores are -inf or invalid
        return {hid: 1.0 / len(horse_scores) for hid in horse_scores}
    
    probabilities = {hid: exp_val / exp_sum for hid, exp_val in exps.items()}
    
    return probabilities


def race_confidence_score(race: Dict, horse_scores: Dict[str, float], probabilities: Dict[str, float]) -> RaceConfidence:
    """
    Compute race-level confidence from probability distribution.
    
    Factors:
    - top_probability: Favorite's win probability
    - probability_gap: Difference between top 2 horses
    - data_quality: How complete the data is
    - pace_stability: How predictable the pace is
    - field_competitiveness: How close the top horses are
    """
    if not probabilities:
        return RaceConfidence(
            level="Low",
            top_probability=0.0,
            probability_gap=0.0,
            data_quality=0.0,
            pace_stability=0.0,
            field_competitiveness=0.0,
        )
    
    # Sort by probability
    sorted_probs = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
    
    top_prob = sorted_probs[0][1] if sorted_probs else 0.0
    second_prob = sorted_probs[1][1] if len(sorted_probs) > 1 else 0.0
    prob_gap = top_prob - second_prob
    
    # Data quality: estimate from number of starters with data
    horses_with_data = len([h for h in race.get("horses", []) if h.get("speed_ratings")])
    total_horses = len(race.get("horses", []))
    data_quality = horses_with_data / total_horses if total_horses > 0 else 0.0
    
    # Pace stability: derived from pace scenario (lower variance = more stable)
    pace_adjustments = race.get("pace_adjustments", {})
    if pace_adjustments:
        adjustments = list(pace_adjustments.values())
        pace_variance = np.var(adjustments) if adjustments else 0.0
        pace_stability = max(0.0, 1.0 - pace_variance)  # Lower variance = higher stability
    else:
        pace_stability = 0.5
    
    # Field competitiveness: inverse of gap between top 2
    # Large gap = dominant favorite = less competitive field
    field_competitiveness = 1.0 - min(1.0, prob_gap * 2)
    
    # Determine confidence level
    confidence_score = (
        top_prob * 0.4 +
        prob_gap * 0.2 +
        data_quality * 0.2 +
        pace_stability * 0.1 +
        (1.0 - field_competitiveness) * 0.1
    )
    
    if confidence_score > 0.65:
        level = "High"
    elif confidence_score > 0.45:
        level = "Medium"
    else:
        level = "Low"
    
    return RaceConfidence(
        level=level,
        top_probability=top_prob,
        probability_gap=prob_gap,
        data_quality=data_quality,
        pace_stability=pace_stability,
        field_competitiveness=field_competitiveness,
    )
