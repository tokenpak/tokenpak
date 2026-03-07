"""
TrackEdge Probability Engine — Softmax win probabilities for a race field.
"""

import numpy as np
from typing import Dict

# Softmax temperature: lower = sharper distribution, higher = flatter
DEFAULT_TEMPERATURE = 15.0


def softmax_probabilities(
    horse_scores: Dict[str, float],
    temperature: float = DEFAULT_TEMPERATURE,
) -> Dict[str, float]:
    """
    Convert power scores (0-100) to win probabilities using softmax.

    p_i = exp(score_i / T) / Σ exp(score_j / T)

    Guarantees Σp_i == 1.0 (enforced via renormalization).

    Args:
        horse_scores: {horse_id: power_score (0–100)}
        temperature:  Softmax temperature T (default 15)

    Returns:
        {horse_id: win_probability (0–1)} — always sums to 1.0
    """
    if not horse_scores:
        return {}

    # Single-horse edge case
    if len(horse_scores) == 1:
        return {next(iter(horse_scores)): 1.0}

    ids = list(horse_scores.keys())
    scores = np.array([horse_scores[hid] for hid in ids], dtype=float)

    # Handle NaN/inf gracefully — replace with median score
    valid_mask = np.isfinite(scores)
    if not valid_mask.any():
        # All invalid → uniform
        return {hid: 1.0 / len(ids) for hid in ids}
    median_score = float(np.median(scores[valid_mask]))
    scores = np.where(valid_mask, scores, median_score)

    # Numerically stable softmax: subtract max before exp
    shifted = scores / temperature
    shifted -= shifted.max()
    exps = np.exp(shifted)

    total = exps.sum()
    if total == 0 or not np.isfinite(total):
        return {hid: 1.0 / len(ids) for hid in ids}

    probs = exps / total

    # Force exact sum-to-1 (floating point safety)
    probs = probs / probs.sum()

    return {hid: float(p) for hid, p in zip(ids, probs)}


def top_contenders(
    probabilities: Dict[str, float],
    n: int = 3,
) -> list:
    """Return the top-n horses by win probability."""
    return sorted(probabilities.items(), key=lambda x: x[1], reverse=True)[:n]
