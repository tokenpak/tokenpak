"""Regression: feedback uses the telemetry Elo implementation (2026-10-16)."""

from tokenpak.proxy.shadow_hook import ShadowHook
from tokenpak.telemetry import elo


def test_feedback_persists_outcome_and_updates_rating(tmp_path, monkeypatch):
    ratings = elo.EloRatings(str(tmp_path / "elo.json"))
    monkeypatch.setattr(elo, "update_elo", ratings.update_elo)
    hook = ShadowHook(str(tmp_path / "routing.db"))
    key = hook.record_request("test-model", "write a function", 100)
    transaction = hook.record_response(key, "response", 50)
    row = hook._ledger.get_transaction(transaction)
    initial = ratings.get_elo(row["model_used"], row["task_type"])
    assert hook.record_feedback(transaction, accepted=True)
    assert hook._ledger.get_transaction(transaction)["accepted"] == 1
    assert ratings.get_elo(row["model_used"], row["task_type"]) > initial
