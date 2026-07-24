"""Unit tests for Autonomous Broker (Phase 3.2)."""

import pytest

pytest.importorskip("tokenpak.broker", reason="module not available in current build")
import os
import tempfile

import pytest
from tokenpak.broker import (
    DOWNGRADE_COOLDOWN,
    Broker,
    cheaper_models,
    get_tier,
    more_capable_models,
)
from tokenpak.routing_ledger import RoutingLedger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIERS = {
    "cheap-model": 1,
    "mid-model": 2,
    "premium-model": 3,
}

_TIERS_PATH = None  # Will be set in setup


def _make_broker(tmpdir, tiers=None, min_samples=5):
    """Create a Broker with a temp ledger/elo, optional custom tiers."""
    tiers = tiers or _TIERS
    # Write tiers to a temp file
    import json

    tiers_path = os.path.join(tmpdir, "model_tiers.json")
    with open(tiers_path, "w") as f:
        json.dump(tiers, f)

    broker = Broker(
        ledger_path=os.path.join(tmpdir, "ledger.db"),
        elo_path=os.path.join(tmpdir, "elo.json"),
        tiers_path=tiers_path,
        min_samples=min_samples,
    )
    return broker


def _flood_ledger(
    ledger: RoutingLedger, model: str, task_type: str, n_accepted: int, n_rejected: int = 0
):
    """Log N accepted + M rejected transactions for a (model, task_type)."""
    # Query that produces the right task_type
    query_map = {
        "CODING": "write a function to sort a list",
        "REASONING": "analyze the tradeoffs between approaches",
        "QA": "what is the capital of France",
        "SUMMARIZATION": "summarize this document",
        "CREATIVE": "write a blog post about AI",
        "UNKNOWN": "something",
    }
    query = query_map.get(task_type, "generic query")
    for _ in range(n_accepted):
        txn_id = ledger.log_transaction(model, query, [], "response")
        ledger.record_outcome(txn_id, accepted=True)
    for _ in range(n_rejected):
        txn_id = ledger.log_transaction(model, query, [], "response")
        ledger.record_outcome(txn_id, accepted=False)


# ---------------------------------------------------------------------------
# Model tier helpers
# ---------------------------------------------------------------------------


class TestModelTiers:
    def test_get_tier_known_model(self):
        assert get_tier("cheap-model", _TIERS) == 1
        assert get_tier("mid-model", _TIERS) == 2
        assert get_tier("premium-model", _TIERS) == 3

    def test_get_tier_unknown_defaults_to_2(self):
        assert get_tier("unknown-model", _TIERS) == 2

    def test_get_tier_strips_provider_prefix(self):
        tiers = {"claude-sonnet": 2}
        assert get_tier("anthropic/claude-sonnet", tiers) == 2

    def test_cheaper_models_sorted(self):
        cheap = cheaper_models("premium-model", _TIERS)
        assert "cheap-model" in cheap
        assert "mid-model" in cheap
        assert "premium-model" not in cheap

    def test_cheaper_models_empty_for_cheapest(self):
        cheap = cheaper_models("cheap-model", _TIERS)
        assert cheap == []

    def test_more_capable_models(self):
        caps = more_capable_models("cheap-model", _TIERS)
        assert "mid-model" in caps or "premium-model" in caps
        assert "cheap-model" not in caps

    def test_more_capable_empty_for_top(self):
        assert more_capable_models("premium-model", _TIERS) == []


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------


class TestConfidenceGate:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_passthrough_below_threshold(self):
        # 0 samples → passthrough
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert decision.action == "passthrough"
        assert "0/5 samples" in decision.reason

    def test_is_confident_false_below_threshold(self):
        assert not self.broker.is_confident("mid-model", "CODING")

    def test_is_confident_true_at_threshold(self):
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        assert self.broker.is_confident("mid-model", "CODING")

    def test_passthrough_when_not_confident(self):
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=3)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert decision.action == "passthrough"


# ---------------------------------------------------------------------------
# Downgrade logic
# ---------------------------------------------------------------------------


class TestDowngrade:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_downgrade_triggers_on_high_acceptance(self):
        # cheap-model has >95% acceptance on CODING
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        # mid-model has enough samples to be confident about itself
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)

        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert decision.action == "downgrade"
        assert decision.selected_model == "cheap-model"

    def test_no_downgrade_below_threshold(self):
        # cheap-model only 80% acceptance (< 95%)
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=8, n_rejected=2)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert decision.action != "downgrade"

    def test_downgrade_badge_contains_model_name(self):
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        if decision.action == "downgrade":
            assert "cheap-model" in decision.badge
            assert "🟢" in decision.badge

    def test_downgrade_logs_routing_action(self):
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        # Decision carries the action
        if decision.action == "downgrade":
            assert decision.selected_model == "cheap-model"
            assert decision.original_model == "mid-model"

    def test_no_downgrade_when_cheap_has_insufficient_samples(self):
        # cheap-model only 3 samples (< min_samples=5) — not enough data
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=3)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert decision.action != "downgrade"


# ---------------------------------------------------------------------------
# Upgrade logic
# ---------------------------------------------------------------------------


class TestUpgrade:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_upgrade_triggers_on_complex_low_acceptance(self):
        # mid-model: 40% acceptance on REASONING (< 50%)
        _flood_ledger(self.broker._ledger, "mid-model", "REASONING", n_accepted=2, n_rejected=3)
        # complexity > 7.0 → should upgrade to premium-model
        decision = self.broker.route("mid-model", "REASONING", complexity_score=8.0)
        assert decision.action == "upgrade"
        assert decision.selected_model == "premium-model"

    def test_no_upgrade_below_complexity_threshold(self):
        # mid-model: 40% acceptance but complexity is low
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=2, n_rejected=3)
        decision = self.broker.route("mid-model", "CODING", complexity_score=5.0)
        assert decision.action != "upgrade"

    def test_no_upgrade_when_acceptance_adequate(self):
        # mid-model: 70% acceptance (> 50%) even though complexity is high
        _flood_ledger(self.broker._ledger, "mid-model", "REASONING", n_accepted=7, n_rejected=3)
        decision = self.broker.route("mid-model", "REASONING", complexity_score=9.0)
        assert decision.action != "upgrade"

    def test_upgrade_badge_present(self):
        _flood_ledger(self.broker._ledger, "mid-model", "REASONING", n_accepted=2, n_rejected=3)
        decision = self.broker.route("mid-model", "REASONING", complexity_score=8.0)
        if decision.action == "upgrade":
            assert "🔵" in decision.badge
            assert "premium-model" in decision.badge

    def test_no_upgrade_from_top_tier(self):
        # premium-model: 40% acceptance, but nothing above it
        _flood_ledger(self.broker._ledger, "premium-model", "REASONING", n_accepted=2, n_rejected=3)
        decision = self.broker.route("premium-model", "REASONING", complexity_score=9.0)
        assert decision.action != "upgrade"


# ---------------------------------------------------------------------------
# force_model bypass
# ---------------------------------------------------------------------------


class TestForceModel:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_force_model_bypasses_all_routing(self):
        # Even with sufficient data, force_model skips everything
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        decision = self.broker.route("mid-model", "CODING", complexity_score=3.0, force_model=True)
        assert decision.action == "passthrough"
        assert decision.selected_model == "mid-model"
        assert "force_model" in decision.reason

    def test_force_model_no_badge(self):
        decision = self.broker.route("mid-model", "CODING", 3.0, force_model=True)
        assert decision.badge == ""


# ---------------------------------------------------------------------------
# Cooldown after rejected downgrade
# ---------------------------------------------------------------------------


class TestCooldown:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_cooldown_after_rejected_downgrade(self):
        # Set up a downgrade scenario
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)

        # First route → should downgrade
        d1 = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert d1.action == "downgrade"

        # Log the downgrade transaction explicitly with routing_action=downgrade
        txn_id = self.broker._ledger.log_transaction(
            "cheap-model",
            "write code",
            [],
            "response",
            routing_action="downgrade",
        )
        # Record rejection → triggers cooldown
        self.broker.record_outcome(txn_id, accepted=False, reason="bad output")

        # Next route should not downgrade (in cooldown)
        d2 = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert d2.action != "downgrade"

    def test_cooldown_expires_after_n_calls(self):
        # Use 50 accepts so 1 rejection still leaves rate > 95% (50/51 ≈ 98%)
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=50)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)

        txn_id = self.broker._ledger.log_transaction(
            "cheap-model", "write code", [], "", routing_action="downgrade"
        )
        self.broker.record_outcome(txn_id, accepted=False)

        # Exhaust cooldown via DOWNGRADE_COOLDOWN passthroughs
        for _ in range(DOWNGRADE_COOLDOWN):
            self.broker.route("mid-model", "CODING", complexity_score=3.0)

        # After cooldown expires, downgrade should be possible again
        d = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert d.action == "downgrade"


# ---------------------------------------------------------------------------
# record_outcome + Elo integration
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_record_outcome_accepted_updates_elo(self):
        txn_id = self.broker._ledger.log_transaction("mid-model", "q", [], "r")
        txn = self.broker._ledger.get_transaction(txn_id)
        task_type = txn["task_type"]
        before = self.broker._elo.get_elo("mid-model", task_type)
        self.broker.record_outcome(txn_id, accepted=True)
        after = self.broker._elo.get_elo("mid-model", task_type)
        assert after > before

    def test_record_outcome_rejected_updates_elo(self):
        txn_id = self.broker._ledger.log_transaction("mid-model", "q", [], "r")
        txn = self.broker._ledger.get_transaction(txn_id)
        task_type = txn["task_type"]
        before = self.broker._elo.get_elo("mid-model", task_type)
        self.broker.record_outcome(txn_id, accepted=False)
        after = self.broker._elo.get_elo("mid-model", task_type)
        assert after < before

    def test_record_outcome_returns_true_on_success(self):
        txn_id = self.broker._ledger.log_transaction("mid-model", "q", [], "r")
        result = self.broker.record_outcome(txn_id, accepted=True)
        assert result is True

    def test_record_outcome_returns_false_for_missing_txn(self):
        result = self.broker.record_outcome(99999, accepted=True)
        assert result is False


# ---------------------------------------------------------------------------
# RoutingDecision fields
# ---------------------------------------------------------------------------


class TestRoutingDecision:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.broker = _make_broker(self.tmpdir, min_samples=5)

    def test_passthrough_fields(self):
        d = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        assert d.original_model == "mid-model"
        assert d.selected_model == "mid-model"
        assert isinstance(d.confidence, float) and 0.0 <= d.confidence <= 1.0
        assert isinstance(d.reason, str) and d.reason

    def test_downgrade_fields_populated(self):
        _flood_ledger(self.broker._ledger, "cheap-model", "CODING", n_accepted=10)
        _flood_ledger(self.broker._ledger, "mid-model", "CODING", n_accepted=5)
        d = self.broker.route("mid-model", "CODING", complexity_score=3.0)
        if d.action == "downgrade":
            assert d.original_model != d.selected_model
            assert d.badge != ""
            assert d.confidence > 0.0
