"""Unit tests for Shadow Mode (Phase 3.1) — complexity, ledger, Elo."""

import json
import os
import tempfile
import threading
import time

import pytest

from tokenpak.complexity import score_complexity, TaskType, _classify_task_type
from tokenpak.routing_ledger import RoutingLedger
from tokenpak.elo import EloRatings, INITIAL_RATING, K_FACTOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ledger(tmpdir):
    return RoutingLedger(os.path.join(tmpdir, "routing_ledger.db"))

def make_elo(tmpdir):
    return EloRatings(os.path.join(tmpdir, "elo_ratings.json"))


# ---------------------------------------------------------------------------
# Complexity scoring
# ---------------------------------------------------------------------------

class TestComplexityScoring:
    def test_simple_query_low_score(self):
        score, _ = score_complexity("What is Python?")
        assert score < 3.0, f"Simple query scored {score}, expected < 3.0"

    def test_long_multistep_query_high_score(self):
        query = (
            "First, analyze the authentication module, then refactor the "
            "login function to use async/await, and also optimize the "
            "database queries, then write unit tests for each method."
        )
        score, _ = score_complexity(query)
        assert score >= 4.0, f"Complex multi-step query scored {score}, expected >= 4.0"

    def test_code_context_boosts_score(self):
        query = "Fix the bug"
        no_code_score, _ = score_complexity(query, [])
        code_context = ["```python\ndef authenticate(user, pwd):\n    pass\n```"]
        code_score, _ = score_complexity(query, code_context)
        assert code_score > no_code_score

    def test_coding_task_type(self):
        query = "Debug the authentication function and refactor the code"
        _, task_type = score_complexity(query)
        assert task_type == TaskType.CODING

    def test_reasoning_task_type(self):
        query = "Analyze the tradeoffs between microservices and monolith architecture"
        _, task_type = score_complexity(query)
        assert task_type == TaskType.REASONING

    def test_summarization_task_type(self):
        query = "Summarize the key points of this document"
        _, task_type = score_complexity(query, ["a " * 600])  # Long context
        assert task_type == TaskType.SUMMARIZATION

    def test_qa_task_type(self):
        query = "What is the capital of France?"
        _, task_type = score_complexity(query)
        assert task_type == TaskType.QA

    def test_creative_task_type(self):
        query = "Write a blog post about machine learning"
        _, task_type = score_complexity(query)
        assert task_type == TaskType.CREATIVE

    def test_score_within_bounds(self):
        for query in ["hi", "a " * 200, "debug optimize refactor design architect"]:
            score, _ = score_complexity(query)
            assert 0.0 <= score <= 10.0, f"Score {score} out of range for: {query[:30]}"

    def test_empty_query_low_score(self):
        score, tt = score_complexity("")
        assert score < 2.0
        assert tt == TaskType.UNKNOWN

    def test_explicit_complexity_boosters(self):
        boosted = "optimize and refactor the database design and architect a new security layer"
        plain = "look at this file"
        s_boosted, _ = score_complexity(boosted)
        s_plain, _ = score_complexity(plain)
        assert s_boosted > s_plain

    def test_no_context_vs_large_context(self):
        query = "What does this code do?"
        score_no_ctx, _ = score_complexity(query, [])
        score_ctx, _ = score_complexity(query, ["word " * 600])
        assert score_ctx >= score_no_ctx


# ---------------------------------------------------------------------------
# RoutingLedger
# ---------------------------------------------------------------------------

class TestRoutingLedger:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger = make_ledger(self.tmpdir)

    def test_wal_mode_active(self):
        assert self.ledger.wal_mode_active(), "WAL mode should be active"

    def test_log_and_retrieve(self):
        txn_id = self.ledger.log_transaction(
            model="claude-sonnet",
            query="Write a function to sort a list",
            context_blocks=["def bubble_sort(): pass"],
            response="Here is a sort function...",
            latency_ms=250.0,
            context_tokens=120,
            response_tokens=80,
        )
        assert txn_id is not None and txn_id > 0
        txn = self.ledger.get_transaction(txn_id)
        assert txn is not None
        assert txn["model_used"] == "claude-sonnet"
        assert txn["latency_ms"] == 250.0
        assert txn["context_tokens"] == 120
        assert txn["response_tokens"] == 80

    def test_accepted_defaults_to_none(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "hi", [], "hello")
        txn = self.ledger.get_transaction(txn_id)
        assert txn["accepted"] is None

    def test_accepted_true_stored(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "hi", [], "hello", accepted=True)
        txn = self.ledger.get_transaction(txn_id)
        assert txn["accepted"] == 1

    def test_accepted_false_stored(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "hi", [], "hello", accepted=False)
        txn = self.ledger.get_transaction(txn_id)
        assert txn["accepted"] == 0

    def test_record_outcome_updates_row(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "q", [], "r")
        assert self.ledger.get_transaction(txn_id)["accepted"] is None
        self.ledger.record_outcome(txn_id, accepted=True)
        assert self.ledger.get_transaction(txn_id)["accepted"] == 1

    def test_record_outcome_with_reason(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "q", [], "r")
        self.ledger.record_outcome(txn_id, False, "hallucination")
        txn = self.ledger.get_transaction(txn_id)
        assert txn["accepted"] == 0
        assert txn["rejection_reason"] == "hallucination"

    def test_task_type_auto_scored(self):
        txn_id = self.ledger.log_transaction(
            "gpt-4o", "Write unit tests for auth module", [], ""
        )
        txn = self.ledger.get_transaction(txn_id)
        assert txn["task_type"] in [tt.value for tt in TaskType]

    def test_complexity_score_stored(self):
        txn_id = self.ledger.log_transaction("gpt-4o", "debug this", [], "")
        txn = self.ledger.get_transaction(txn_id)
        assert 0.0 <= txn["complexity_score"] <= 10.0

    def test_context_weight_computed(self):
        txn_id = self.ledger.log_transaction(
            "gpt-4o", "q", [], "r", context_tokens=900, response_tokens=100
        )
        txn = self.ledger.get_transaction(txn_id)
        assert abs(txn["context_weight"] - 0.9) < 0.01

    def test_get_recent(self):
        for i in range(5):
            self.ledger.log_transaction(f"model-{i}", f"query {i}", [], "resp")
        recent = self.ledger.get_recent(3)
        assert len(recent) == 3

    def test_get_stats(self):
        self.ledger.log_transaction("gpt-4o", "q1", [], "r1", accepted=True)
        self.ledger.log_transaction("gpt-4o", "q2", [], "r2", accepted=False)
        self.ledger.log_transaction("claude", "q3", [], "r3")
        stats = self.ledger.get_stats()
        assert stats["total"] == 3
        assert stats["accepted"] == 1
        assert stats["rejected"] == 1
        assert stats["unreviewed"] == 1
        assert "gpt-4o" in stats["by_model"]

    def test_sample_count(self):
        for _ in range(3):
            self.ledger.log_transaction("gpt-4o", "debug code", [], "", accepted=True)
        txn_id = self.ledger.log_transaction("gpt-4o", "debug code", [], "")
        # Task type will be CODING for "debug code"
        txn = self.ledger.get_transaction(txn_id)
        task_type = txn["task_type"]
        count = self.ledger.sample_count("gpt-4o", task_type)
        assert count >= 3

    def test_acceptance_rate(self):
        # 3 wins, 1 loss for gpt-4o/CODING
        for _ in range(3):
            self.ledger.log_transaction("gpt-4o", "write code", [], "", accepted=True)
        self.ledger.log_transaction("gpt-4o", "write code", [], "", accepted=False)
        txn = self.ledger.get_transaction(
            self.ledger.log_transaction("gpt-4o", "write code", [], "")
        )
        rate = self.ledger.acceptance_rate("gpt-4o", txn["task_type"])
        # Should be roughly 75% (3/4 accepted)
        assert 0.5 <= rate <= 1.0

    def test_thread_safe_concurrent_writes(self):
        errors = []
        def write_txn():
            try:
                self.ledger.log_transaction("model-x", "concurrent query", [], "response")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=write_txn) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        stats = self.ledger.get_stats()
        assert stats["total"] == 10


# ---------------------------------------------------------------------------
# Elo ratings
# ---------------------------------------------------------------------------

class TestEloRatings:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.elo = make_elo(self.tmpdir)

    def test_initial_rating_is_1200(self):
        rating = self.elo.get_elo("new-model", "CODING")
        assert rating == INITIAL_RATING

    def test_win_increases_rating(self):
        before = self.elo.get_elo("gpt-4o", "CODING")
        after = self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        assert after > before

    def test_loss_decreases_rating(self):
        before = self.elo.get_elo("gpt-4o", "CODING")
        after = self.elo.update_elo("gpt-4o", "CODING", accepted=False)
        assert after < before

    def test_ratings_persist_to_disk(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        rating1 = self.elo.get_elo("gpt-4o", "CODING")
        # Reload from disk
        elo2 = EloRatings(self.elo.ratings_path)
        rating2 = elo2.get_elo("gpt-4o", "CODING")
        assert abs(rating1 - rating2) < 0.001

    def test_task_types_are_independent(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        coding_rating = self.elo.get_elo("gpt-4o", "CODING")
        reasoning_rating = self.elo.get_elo("gpt-4o", "REASONING")
        assert coding_rating != reasoning_rating  # coding got win, reasoning untouched

    def test_models_are_independent(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        r_gpt = self.elo.get_elo("gpt-4o", "CODING")
        r_claude = self.elo.get_elo("claude-sonnet", "CODING")
        assert r_gpt != r_claude  # gpt got win, claude untouched

    def test_convergence_after_consistent_wins(self):
        for _ in range(20):
            self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        rating = self.elo.get_elo("gpt-4o", "CODING")
        assert rating > INITIAL_RATING + 50, f"Expected significant gain, got {rating}"

    def test_convergence_after_consistent_losses(self):
        for _ in range(20):
            self.elo.update_elo("gpt-4o", "CODING", accepted=False)
        rating = self.elo.get_elo("gpt-4o", "CODING")
        assert rating < INITIAL_RATING - 50, f"Expected significant drop, got {rating}"

    def test_get_rankings(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        self.elo.update_elo("claude-haiku", "CODING", accepted=False)
        rankings = self.elo.get_rankings(task_type="CODING")
        assert len(rankings) == 2
        # gpt-4o should rank higher (two wins)
        assert rankings[0][0] == "gpt-4o"

    def test_get_all_returns_dict(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        all_ratings = self.elo.get_all()
        assert isinstance(all_ratings, dict)
        assert len(all_ratings) >= 1

    def test_reset_specific_model(self):
        self.elo.update_elo("gpt-4o", "CODING", accepted=True)
        self.elo.update_elo("claude", "CODING", accepted=True)
        self.elo.reset(model="gpt-4o")
        assert self.elo.get_elo("gpt-4o", "CODING") == INITIAL_RATING
        assert self.elo.get_elo("claude", "CODING") != INITIAL_RATING

    def test_enum_task_type_accepted(self):
        # TaskType enum should also work (not just string)
        rating = self.elo.update_elo("gpt-4o", TaskType.CODING, accepted=True)
        assert rating > INITIAL_RATING

    def test_k_factor_delta_magnitude(self):
        # Starting from 1200 vs benchmark 1200: expected = 0.5
        # Delta = K * (1.0 - 0.5) = K * 0.5 = 16.0
        before = INITIAL_RATING
        after = self.elo.update_elo("fresh-model", "QA", accepted=True)
        expected_delta = K_FACTOR * 0.5
        assert abs(after - before - expected_delta) < 0.01


# ---------------------------------------------------------------------------
# ShadowHook integration
# ---------------------------------------------------------------------------

class TestShadowHook:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        from tokenpak.shadow_hook import ShadowHook
        self.hook = ShadowHook(
            ledger_path=os.path.join(self.tmpdir, "routing_ledger.db"),
            enabled=True,
        )

    def test_record_request_returns_key(self):
        key = self.hook.record_request("gpt-4o", "test query", context_tokens=100)
        assert key is not None

    def test_record_response_commits_transaction(self):
        key = self.hook.record_request("gpt-4o", "write a function", 200)
        txn_id = self.hook.record_response(key, "Here is the function...", 80)
        assert txn_id is not None and txn_id > 0

    def test_record_feedback_updates_elo(self):
        key = self.hook.record_request("gpt-4o", "code task", 100)
        txn_id = self.hook.record_response(key, "response", 50)
        assert txn_id is not None
        result = self.hook.record_feedback(txn_id, accepted=True)
        assert result is True

    def test_disabled_hook_returns_none(self):
        from tokenpak.shadow_hook import ShadowHook
        hook = ShadowHook(enabled=False)
        assert hook.record_request("gpt-4o", "q", 0) is None

    def test_none_key_record_response_safe(self):
        # Should not crash
        result = self.hook.record_response(None, "response", 50)
        assert result is None
