# SPDX-License-Identifier: Apache-2.0
"""Integration tests — verify the Broker reads and updates Elo ratings during routing.

These tests confirm:
  1. Elo scores update after a broker decision via record_outcome (feedback loop wired).
  2. Broker falls back to passthrough when Elo/ledger data is missing (cold start).
  3. Broker downgrade wires Elo feedback on the model_used (cheaper model).
  4. Two models with equal Elo — broker uses acceptance-rate tie-breaking via the ledger.
"""

import json
from pathlib import Path

import pytest

from tokenpak.routing.broker import Broker
from tokenpak.telemetry.elo import INITIAL_RATING

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_TIERS = {
    "claude-haiku-3-5": 1,
    "claude-sonnet-4-5": 2,
    "claude-opus-4-5": 3,
}

# Query strings that score_complexity() maps to a consistent task_type
QUERY_CODING = "refactor the class and implement the changes"

MIN_SAMPLES = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_broker(tmp_path, min_samples=MIN_SAMPLES):
    tiers_file = tmp_path / "model_tiers.json"
    tiers_file.write_text(json.dumps(MODEL_TIERS))
    return Broker(
        ledger_path=str(tmp_path / "ledger.db"),
        elo_path=str(tmp_path / "elo.json"),
        tiers_path=str(tiers_file),
        min_samples=min_samples,
    )


def _prime(broker, model, query, n_accepted, n_rejected=0):
    """Insert synthetic transactions so broker reaches confidence threshold."""
    for _ in range(n_accepted):
        txn_id = broker._ledger.log_transaction(
            model=model, query=query, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker._ledger.record_outcome(txn_id, accepted=True)
    for _ in range(n_rejected):
        txn_id = broker._ledger.log_transaction(
            model=model, query=query, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker._ledger.record_outcome(txn_id, accepted=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEloIntegration:

    def test_elo_updates_on_accepted_outcome(self, tmp_path):
        """Elo rating increases when record_outcome(accepted=True) is called."""
        broker = _make_broker(tmp_path)
        model = "claude-sonnet-4-5"

        before = broker._elo.get_elo(model, "CODING")
        assert before == INITIAL_RATING

        txn_id = broker._ledger.log_transaction(
            model=model, query=QUERY_CODING, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker.record_outcome(txn_id, accepted=True)

        after = broker._elo.get_elo(model, "CODING")
        assert after > before, "Elo should rise on acceptance"

    def test_elo_updates_on_rejected_outcome(self, tmp_path):
        """Elo rating decreases when record_outcome(accepted=False) is called."""
        broker = _make_broker(tmp_path)
        model = "claude-sonnet-4-5"

        before = broker._elo.get_elo(model, "CODING")
        txn_id = broker._ledger.log_transaction(
            model=model, query=QUERY_CODING, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker.record_outcome(txn_id, accepted=False)

        after = broker._elo.get_elo(model, "CODING")
        assert after < before, "Elo should fall on rejection"

    def test_broker_passthrough_when_no_history(self, tmp_path):
        """Broker passes through on cold start — no ledger data, no Elo history."""
        broker = _make_broker(tmp_path)
        decision = broker.route(
            model="claude-sonnet-4-5",
            task_type="CODING",
            complexity_score=5.0,
        )
        assert decision.action == "passthrough"
        assert decision.selected_model == "claude-sonnet-4-5"

    def test_downgrade_wires_elo_feedback_on_cheap_model(self, tmp_path):
        """After a successful downgrade, Elo for the cheap model rises on positive outcome."""
        broker = _make_broker(tmp_path, min_samples=MIN_SAMPLES)
        cheap = "claude-haiku-3-5"
        sonnet = "claude-sonnet-4-5"

        # Prime both models: sonnet needs samples for confidence gate, cheap needs high acceptance
        _prime(broker, sonnet, QUERY_CODING, n_accepted=MIN_SAMPLES)
        _prime(broker, cheap, QUERY_CODING, n_accepted=MIN_SAMPLES)

        decision = broker.route(
            model=sonnet,
            task_type="CODING",
            complexity_score=5.0,
        )
        assert decision.action == "downgrade", f"Expected downgrade, got: {decision.action}"
        assert decision.selected_model == cheap

        # Log the actual downgraded transaction so record_outcome can look it up
        txn_id = broker._ledger.log_transaction(
            model=cheap, query=QUERY_CODING, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=80.0, routing_action="downgrade",
        )
        elo_before = broker._elo.get_elo(cheap, "CODING")
        broker.record_outcome(txn_id, accepted=True)
        elo_after = broker._elo.get_elo(cheap, "CODING")

        assert elo_after > elo_before, "Elo for downgraded model should rise on accepted outcome"

    def test_equal_elo_broker_uses_acceptance_rate_tiebreaker(self, tmp_path):
        """With equal Elo, broker uses acceptance rate (ledger) as tiebreaker.

        Both models start at INITIAL_RATING (tied Elo). The broker's downgrade
        decision is driven by the ledger acceptance rate, not Elo — confirming
        the acceptance-rate path is the tie-breaker when Elo is equal.
        """
        broker = _make_broker(tmp_path, min_samples=MIN_SAMPLES)
        cheap = "claude-haiku-3-5"
        sonnet = "claude-sonnet-4-5"

        # Both models start at INITIAL_RATING (equal Elo)
        assert broker._elo.get_elo(cheap, "CODING") == INITIAL_RATING
        assert broker._elo.get_elo(sonnet, "CODING") == INITIAL_RATING

        # Prime both: sonnet needs samples for confidence gate; cheap gets perfect acceptance rate
        _prime(broker, sonnet, QUERY_CODING, n_accepted=MIN_SAMPLES)
        _prime(broker, cheap, QUERY_CODING, n_accepted=MIN_SAMPLES)

        decision = broker.route(
            model=sonnet,
            task_type="CODING",
            complexity_score=5.0,
        )
        # Broker downgrades based on acceptance rate even when Elo is tied
        assert decision.action == "downgrade"
        assert decision.selected_model == cheap
        assert decision.confidence > 0.0
