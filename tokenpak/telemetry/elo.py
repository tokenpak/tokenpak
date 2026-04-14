# SPDX-License-Identifier: Apache-2.0
"""Elo Rating System for TokenPak Shadow Mode.

Maintains per-model Elo ratings per task_type. Updated on each logged
transaction. Persisted to .tokenpak/elo_ratings.json.

Standard Elo formula:
  E_a = 1 / (1 + 10^((R_b - R_a) / 400))
  R_a' = R_a + K * (S_a - E_a)

Where K=32, S_a=1 (win / accepted), S_a=0 (loss / rejected).
"""

import json
from pathlib import Path
from typing import Optional

DEFAULT_ELO_PATH = ".tokenpak/elo_ratings.json"

INITIAL_RATING = 1200.0
K_FACTOR = 32.0

# Sentinel: rating for an "average opponent" (used when a model has no
# head-to-head; we rate against a fixed benchmark at 1200).
_BENCHMARK_RATING = 1200.0


class EloRatings:
    """
    Persistent Elo rating store.
    Ratings are keyed by (model, task_type) → float.
    """

    def __init__(self, ratings_path: str = DEFAULT_ELO_PATH):
        self.ratings_path = ratings_path
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_elo(self, model: str, task_type: str) -> float:
        """
        Return current Elo rating for (model, task_type).
        Initializes at INITIAL_RATING if unseen.
        """
        key = self._key(model, task_type)
        return self._data.get(key, INITIAL_RATING)

    def update_elo(self, model: str, task_type: str, accepted: bool) -> float:
        """
        Update Elo rating for a model after a transaction outcome.

        Args:
            model:      Model name.
            task_type:  Task type string (TaskType enum value or str).
            accepted:   True=win (accepted), False=loss (rejected).

        Returns:
            Updated rating.
        """
        key = self._key(model, task_type)
        r_model = self._data.get(key, INITIAL_RATING)

        # Expected score against benchmark opponent
        expected = self._expected(r_model, _BENCHMARK_RATING)
        actual = 1.0 if accepted else 0.0

        new_rating = r_model + K_FACTOR * (actual - expected)
        self._data[key] = round(new_rating, 4)
        self._save()
        return self._data[key]

    def get_all(self) -> dict:
        """Return a copy of all ratings."""
        return dict(self._data)

    def get_rankings(self, task_type: Optional[str] = None) -> list:
        """
        Return sorted list of (model, task_type, rating) tuples.
        Optionally filtered to a specific task_type.
        """
        rows = []
        for key, rating in self._data.items():
            model, tt = self._parse_key(key)
            if task_type and tt != task_type:
                continue
            rows.append((model, tt, rating))
        return sorted(rows, key=lambda r: r[2], reverse=True)

    def reset(self, model: Optional[str] = None, task_type: Optional[str] = None):
        """Reset ratings. Pass model and/or task_type to reset selectively."""
        if model is None and task_type is None:
            self._data = {}
        else:
            keys_to_del = []
            for key in list(self._data.keys()):
                m, tt = self._parse_key(key)
                if (model is None or m == model) and (task_type is None or tt == task_type):
                    keys_to_del.append(key)
            for k in keys_to_del:
                del self._data[k]
        self._save()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _key(model: str, task_type: str) -> str:
        # Normalize
        tt = task_type.upper() if isinstance(task_type, str) else task_type.value  # type: ignore[attr-defined]
        return f"{model.lower()}::{tt}"

    @staticmethod
    def _parse_key(key: str) -> tuple:
        parts = key.split("::", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else (key, "UNKNOWN")

    @staticmethod
    def _expected(rating_a: float, rating_b: float) -> float:
        """Expected score for player A against player B."""
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))

    def _load(self) -> dict:
        p = Path(self.ratings_path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        p = Path(self.ratings_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._data, indent=2))


# ---------------------------------------------------------------------------
# Module-level convenience functions (single shared instance)
# ---------------------------------------------------------------------------

_default_elo: Optional[EloRatings] = None


def _get_default(elo_path: str = DEFAULT_ELO_PATH) -> EloRatings:
    global _default_elo
    if _default_elo is None or _default_elo.ratings_path != elo_path:
        _default_elo = EloRatings(elo_path)
    return _default_elo


def get_elo(model: str, task_type: str, elo_path: str = DEFAULT_ELO_PATH) -> float:
    """Return current Elo rating for (model, task_type)."""
    return _get_default(elo_path).get_elo(model, task_type)


def update_elo(
    model: str, task_type: str, accepted: bool, elo_path: str = DEFAULT_ELO_PATH
) -> float:
    """Update and persist Elo rating. Returns new rating."""
    return _get_default(elo_path).update_elo(model, task_type, accepted)
