# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.broker — Autonomous Routing Broker."""

import json
import threading
from pathlib import Path

import pytest

from tokenpak.broker import (
    DOWNGRADE_COOLDOWN,
    Broker,
    RoutingDecision,
    _load_tiers,
    cheaper_models,
    get_tier,
    more_capable_models,
)

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

MODEL_TIERS = {
    "claude-haiku-3-5": 1,
    "claude-sonnet-4-5": 2,
    "claude-opus-4-5": 3,
}

# Canonical TaskType.value strings stored in the ledger DB
TASK_SUMMARIZE = "SUMMARIZATION"
TASK_CODING = "CODING"

# Queries that score_complexity() maps to the desired TaskType
QUERY_SUMMARIZE = "summarize this document please"
QUERY_CODING = "refactor the class and implement changes"


@pytest.fixture
def tiers_file(tmp_path):
    path = tmp_path / "model_tiers.json"
    path.write_text(json.dumps(MODEL_TIERS))
    return str(path)


@pytest.fixture
def broker(tmp_path, tiers_file):
    """Broker wired to temp SQLite files, min_samples=5."""
    return Broker(
        ledger_path=str(tmp_path / "ledger.db"),
        elo_path=str(tmp_path / "elo.json"),
        tiers_path=tiers_file,
        min_samples=5,
    )


def _prime(broker: Broker, model: str, query: str, n_accepted: int, n_rejected: int = 0):
    """Insert synthetic accepted/rejected transactions into the ledger."""
    for _ in range(n_accepted):
        txn = broker._ledger.log_transaction(
            model=model, query=query, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker._ledger.record_outcome(txn, accepted=True)
    for _ in range(n_rejected):
        txn = broker._ledger.log_transaction(
            model=model, query=query, context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        broker._ledger.record_outcome(txn, accepted=False)


# ---------------------------------------------------------------------------
# _load_tiers
# ---------------------------------------------------------------------------

class TestLoadTiers:
    def test_loads_valid_json(self, tiers_file):
        assert _load_tiers(tiers_file) == MODEL_TIERS

    def test_returns_empty_on_missing_file(self, tmp_path):
        assert _load_tiers(str(tmp_path / "nope.json")) == {}

    def test_returns_empty_on_malformed_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid }")
        assert _load_tiers(str(bad)) == {}


# ---------------------------------------------------------------------------
# get_tier
# ---------------------------------------------------------------------------

class TestGetTier:
    def test_known_model(self):
        assert get_tier("claude-haiku-3-5", MODEL_TIERS) == 1

    def test_provider_prefixed_model(self):
        assert get_tier("anthropic/claude-haiku-3-5", MODEL_TIERS) == 1

    def test_unknown_defaults_to_2(self):
        assert get_tier("unknown-model", MODEL_TIERS) == 2

    def test_empty_tiers(self):
        assert get_tier("claude-haiku-3-5", {}) == 2


# ---------------------------------------------------------------------------
# cheaper_models / more_capable_models
# ---------------------------------------------------------------------------

class TestModelHelpers:
    def test_cheaper_than_opus(self):
        result = cheaper_models("claude-opus-4-5", MODEL_TIERS)
        assert "claude-haiku-3-5" in result
        assert "claude-sonnet-4-5" in result
        assert "claude-opus-4-5" not in result

    def test_cheaper_sorted_cheapest_first(self):
        result = cheaper_models("claude-opus-4-5", MODEL_TIERS)
        tiers = [MODEL_TIERS[m] for m in result]
        assert tiers == sorted(tiers)

    def test_no_cheaper_than_haiku(self):
        assert cheaper_models("claude-haiku-3-5", MODEL_TIERS) == []

    def test_more_capable_than_haiku(self):
        result = more_capable_models("claude-haiku-3-5", MODEL_TIERS)
        assert "claude-sonnet-4-5" in result
        assert "claude-opus-4-5" in result
        assert "claude-haiku-3-5" not in result

    def test_more_capable_sorted_most_capable_first(self):
        result = more_capable_models("claude-haiku-3-5", MODEL_TIERS)
        tiers = [MODEL_TIERS[m] for m in result]
        assert tiers == sorted(tiers, reverse=True)

    def test_no_more_capable_than_opus(self):
        assert more_capable_models("claude-opus-4-5", MODEL_TIERS) == []

    def test_excludes_private_keys(self):
        tiers = {**MODEL_TIERS, "_meta": 99}
        assert "_meta" not in cheaper_models("claude-opus-4-5", tiers)


# ---------------------------------------------------------------------------
# RoutingDecision
# ---------------------------------------------------------------------------

class TestRoutingDecision:
    def test_default_badge_empty(self):
        d = RoutingDecision("s", "s", "passthrough", 1.0, "ok")
        assert d.badge == ""

    def test_all_fields(self):
        d = RoutingDecision("s", "h", "downgrade", 0.9, "cheap", badge="⚡")
        assert d.action == "downgrade"
        assert d.badge == "⚡"


# ---------------------------------------------------------------------------
# force_model
# ---------------------------------------------------------------------------

class TestForceModel:
    def test_returns_passthrough(self, broker):
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0, force_model=True)
        assert d.action == "passthrough"
        assert d.confidence == 1.0

    def test_skips_routing_even_with_data(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=10)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0, force_model=True)
        assert d.action == "passthrough"
        assert d.selected_model == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    def test_no_data_passthrough(self, broker):
        d = broker.route("claude-sonnet-4-5", TASK_CODING, 4.0)
        assert d.action == "passthrough"
        assert d.confidence == 0.0

    def test_partial_samples_passthrough(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_CODING, n_accepted=3)
        d = broker.route("claude-sonnet-4-5", TASK_CODING, 4.0)
        assert d.action == "passthrough"
        assert d.confidence == pytest.approx(3 / 5)

    def test_full_samples_unlocks_routing(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=5)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
        assert d.confidence == 1.0


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_downgrade_when_cheap_model_high_acceptance(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=5)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
        assert d.action == "downgrade"
        assert d.selected_model == "claude-haiku-3-5"
        assert "routed to" in d.badge

    def test_no_downgrade_below_acceptance_threshold(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=3, n_rejected=2)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
        assert d.action != "downgrade"

    def test_no_downgrade_when_already_cheapest(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-haiku-3-5", TASK_SUMMARIZE, 2.0)
        assert d.action != "downgrade"

    def test_no_downgrade_insufficient_samples_on_cheap_model(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=2)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
        assert d.action != "downgrade"


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

class TestUpgrade:
    def test_upgrade_high_complexity_low_acceptance(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_CODING, n_accepted=2, n_rejected=3)
        d = broker.route("claude-sonnet-4-5", TASK_CODING, complexity_score=9.0)
        assert d.action == "upgrade"
        assert d.selected_model == "claude-opus-4-5"
        assert "upgraded" in d.badge

    def test_no_upgrade_low_complexity(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_CODING, n_accepted=1, n_rejected=4)
        d = broker.route("claude-sonnet-4-5", TASK_CODING, complexity_score=3.0)
        assert d.action != "upgrade"

    def test_no_upgrade_adequate_acceptance(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_CODING, n_accepted=4, n_rejected=1)
        d = broker.route("claude-sonnet-4-5", TASK_CODING, complexity_score=9.0)
        assert d.action != "upgrade"

    def test_no_upgrade_already_most_capable(self, broker):
        _prime(broker, "claude-opus-4-5", QUERY_CODING, n_accepted=1, n_rejected=4)
        d = broker.route("claude-opus-4-5", TASK_CODING, complexity_score=9.0)
        assert d.action != "upgrade"


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

class TestCooldown:
    def test_cooldown_set_after_rejected_downgrade(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=5)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        d = broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
        assert d.action == "downgrade"

        txn = broker._ledger.log_transaction(
            model="claude-haiku-3-5", query=QUERY_SUMMARIZE,
            context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
            routing_action="downgrade",
        )
        broker.record_outcome(txn, accepted=False)
        assert broker._cooldown.get("claude-haiku-3-5", 0) == DOWNGRADE_COOLDOWN

    def test_cooldown_not_set_on_accepted_downgrade(self, broker):
        txn = broker._ledger.log_transaction(
            model="claude-haiku-3-5", query=QUERY_SUMMARIZE,
            context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
            routing_action="downgrade",
        )
        broker.record_outcome(txn, accepted=True)
        assert broker._cooldown.get("claude-haiku-3-5", 0) == 0


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_routes_do_not_raise(self, broker):
        _prime(broker, "claude-haiku-3-5", QUERY_SUMMARIZE, n_accepted=5)
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        errors = []

        def _route():
            try:
                broker.route("claude-sonnet-4-5", TASK_SUMMARIZE, 2.0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_route) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# is_confident
# ---------------------------------------------------------------------------

class TestIsConfident:
    def test_not_confident_no_samples(self, broker):
        assert broker.is_confident("claude-sonnet-4-5", TASK_SUMMARIZE) is False

    def test_confident_at_threshold(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=5)
        assert broker.is_confident("claude-sonnet-4-5", TASK_SUMMARIZE) is True

    def test_not_confident_partial_samples(self, broker):
        _prime(broker, "claude-sonnet-4-5", QUERY_SUMMARIZE, n_accepted=3)
        assert broker.is_confident("claude-sonnet-4-5", TASK_SUMMARIZE) is False


# ---------------------------------------------------------------------------
# record_outcome / Elo
# ---------------------------------------------------------------------------

class TestRecordOutcome:
    def test_accepted_increases_elo(self, broker):
        txn = broker._ledger.log_transaction(
            model="claude-sonnet-4-5", query=QUERY_SUMMARIZE,
            context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        before = broker._elo.get_elo("claude-sonnet-4-5", TASK_SUMMARIZE)
        broker.record_outcome(txn, accepted=True)
        after = broker._elo.get_elo("claude-sonnet-4-5", TASK_SUMMARIZE)
        assert after > before

    def test_returns_true_on_valid_txn(self, broker):
        txn = broker._ledger.log_transaction(
            model="claude-sonnet-4-5", query=QUERY_SUMMARIZE,
            context_blocks=[], response="ok",
            context_tokens=500, latency_ms=100.0,
        )
        assert broker.record_outcome(txn, accepted=True) is True

    def test_returns_false_on_invalid_txn(self, broker):
        assert broker.record_outcome(99999, accepted=True) is False
