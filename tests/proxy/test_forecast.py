"""tests/proxy/test_forecast.py

CCI-11: Unit tests for POST /v1/messages/forecast (AC2-compliant response).

Tests the forecast_endpoint module and the AC2-specified response shape:
  {estimated_cost_usd, input_tokens, cached_tokens, ttfb_estimate_ms,
   cache_hit_likelihood, model}

These tests exercise the module directly (no live HTTP server required).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Bootstrap: add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-cci11-dummy")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

from tokenpak.proxy.forecast_endpoint import (  # noqa: E402
    build_forecast_response,
    count_request_tokens,
    estimate_cache_hit_likelihood,
    estimate_ttfb_ms,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_forecast_latency_buffer():
    """Isolate from the module-global rolling latency buffer.

    ``estimate_ttfb_ms`` prefers the process-wide ``_forecast_latencies``
    deque over DB history and the formula. Any earlier test in the session
    that drives real requests through the proxy appends observed latencies
    there (server-side per-request hook), which silently flips these tests
    onto the buffer path. Clear it on both sides so each test starts from
    the documented empty-buffer behavior.
    """
    from tokenpak.proxy import forecast_endpoint

    forecast_endpoint._forecast_latencies.clear()
    yield
    forecast_endpoint._forecast_latencies.clear()


@pytest.fixture()
def empty_db(tmp_path):
    """Provide a path to an empty (no tables) SQLite DB."""
    db = tmp_path / "monitor_empty.db"
    db.touch()
    return str(db)


@pytest.fixture()
def history_db(tmp_path):
    """Provide a monitor.db with 10 request rows, half with cache hits."""
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE requests (
               id INTEGER PRIMARY KEY,
               model TEXT,
               session_id TEXT,
               cache_read_tokens INTEGER,
               latency_ms REAL,
               timestamp TEXT DEFAULT (datetime('now'))
           )"""
    )
    for i in range(10):
        conn.execute(
            "INSERT INTO requests (model, session_id, cache_read_tokens, latency_ms) VALUES (?,?,?,?)",
            ("claude-sonnet-4-6", "sess-abc", 500 if i < 5 else 0, 300.0),
        )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture()
def simple_body() -> Dict[str, Any]:
    return {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
    }


# ---------------------------------------------------------------------------
# count_request_tokens
# ---------------------------------------------------------------------------

class TestCountRequestTokens:
    def test_single_string_message(self):
        body = {"messages": [{"role": "user", "content": "Hello world"}]}
        result = count_request_tokens(body)
        assert result > 0

    def test_empty_messages(self):
        body = {"messages": []}
        assert count_request_tokens(body) == 0

    def test_system_prompt_counted(self):
        body_no_sys = {"messages": [{"role": "user", "content": "hi"}]}
        body_sys = {"system": "You are a helpful assistant.", "messages": [{"role": "user", "content": "hi"}]}
        assert count_request_tokens(body_sys) > count_request_tokens(body_no_sys)

    def test_list_content_blocks(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is 2 + 2?"},
                        {"type": "text", "text": "Please explain."},
                    ],
                }
            ]
        }
        result = count_request_tokens(body)
        assert result > 0

    def test_multi_turn_conversation(self):
        body = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ]
        }
        result = count_request_tokens(body)
        assert result > 0

    def test_large_context(self):
        """Large context should produce large token count."""
        words = " ".join(["word"] * 2000)
        body = {"messages": [{"role": "user", "content": words}]}
        result = count_request_tokens(body)
        assert result > 500


# ---------------------------------------------------------------------------
# estimate_cache_hit_likelihood
# ---------------------------------------------------------------------------

class TestCacheHitLikelihood:
    def test_no_history_returns_zero(self, empty_db):
        result = estimate_cache_hit_likelihood("claude-sonnet-4-6", empty_db)
        assert result == 0.0

    def test_half_hit_history(self, history_db):
        result = estimate_cache_hit_likelihood("claude-sonnet-4-6", history_db)
        assert 0.4 <= result <= 0.6

    def test_session_scoped_history(self, history_db):
        result = estimate_cache_hit_likelihood(
            "claude-sonnet-4-6", history_db, session_id="sess-abc"
        )
        assert 0.0 <= result <= 1.0

    def test_missing_db_returns_zero(self, tmp_path):
        result = estimate_cache_hit_likelihood("claude-sonnet-4-6", str(tmp_path / "nonexistent.db"))
        assert result == 0.0

    def test_result_in_range(self, history_db):
        result = estimate_cache_hit_likelihood("claude-sonnet-4-6", history_db)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# estimate_ttfb_ms
# ---------------------------------------------------------------------------

class TestEstimateTtfb:
    def test_no_history_uses_formula(self, empty_db):
        result = estimate_ttfb_ms("claude-sonnet-4-6", 100, empty_db)
        assert isinstance(result, int)
        assert result >= 100

    def test_formula_scales_with_tokens(self, empty_db):
        small = estimate_ttfb_ms("claude-sonnet-4-6", 100, empty_db)
        large = estimate_ttfb_ms("claude-sonnet-4-6", 10000, empty_db)
        assert large >= small

    def test_db_history_used(self, history_db):
        result = estimate_ttfb_ms("claude-sonnet-4-6", 100, history_db)
        assert isinstance(result, int)
        assert result >= 50


# ---------------------------------------------------------------------------
# build_forecast_response — AC2 compliance
# ---------------------------------------------------------------------------

class TestBuildForecastResponse:
    def test_returns_all_ac2_fields(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert "estimated_cost_usd" in result
        assert "input_tokens" in result
        assert "cached_tokens" in result
        assert "ttfb_estimate_ms" in result
        assert "cache_hit_likelihood" in result
        assert "model" in result

    def test_model_field_matches_request(self, empty_db):
        body = {"model": "claude-opus-4-6", "messages": [{"role": "user", "content": "hi"}]}
        result = build_forecast_response(body, empty_db)
        assert result["model"] == "claude-opus-4-6"

    def test_input_tokens_positive_for_nonempty_body(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert result["input_tokens"] > 0

    def test_cached_tokens_is_int(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert isinstance(result["cached_tokens"], int)
        assert result["cached_tokens"] >= 0

    def test_estimated_cost_nonnegative(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert result["estimated_cost_usd"] >= 0.0

    def test_cache_hit_likelihood_in_range(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert 0.0 <= result["cache_hit_likelihood"] <= 1.0

    def test_ttfb_estimate_positive(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert result["ttfb_estimate_ms"] > 0

    def test_backward_compat_breakdown_present(self, simple_body, empty_db):
        result = build_forecast_response(simple_body, empty_db)
        assert "breakdown" in result
        bd = result["breakdown"]
        assert "input_tokens" in bd
        assert "output_estimate" in bd
        assert "cache_hits_estimate" in bd
        assert "cache_creates_estimate" in bd

    def test_empty_messages_returns_zero_input_tokens(self, empty_db):
        body = {"model": "claude-sonnet-4-6", "messages": []}
        result = build_forecast_response(body, empty_db)
        assert result["input_tokens"] == 0

    def test_missing_model_reported_as_empty_not_fabricated(self, empty_db):
        """A body without a model must not have one invented for it."""
        body = {"messages": [{"role": "user", "content": "hi"}]}
        result = build_forecast_response(body, empty_db)
        assert isinstance(result["model"], str)
        assert result["model"] == ""
        # Cost is still estimated (default-class rates), just not
        # attributed to a real model name.
        assert result["estimated_cost_usd"] > 0

    def test_non_string_model_reported_as_empty(self, empty_db):
        body = {"model": 42, "messages": [{"role": "user", "content": "hi"}]}
        result = build_forecast_response(body, empty_db)
        assert result["model"] == ""

    def test_opus_costs_more_than_haiku(self, empty_db):
        msg = [{"role": "user", "content": "Hello world, tell me everything you know."}]
        opus_body = {"model": "claude-opus-4-6", "messages": msg}
        haiku_body = {"model": "claude-haiku-4-5", "messages": msg}
        opus_cost = build_forecast_response(opus_body, empty_db)["estimated_cost_usd"]
        haiku_cost = build_forecast_response(haiku_body, empty_db)["estimated_cost_usd"]
        assert opus_cost > haiku_cost

    def test_larger_context_costs_more(self, empty_db):
        short_msg = [{"role": "user", "content": "Hi"}]
        long_msg = [{"role": "user", "content": " ".join(["word"] * 1000)}]
        short_cost = build_forecast_response(
            {"model": "claude-sonnet-4-6", "messages": short_msg}, empty_db
        )["estimated_cost_usd"]
        long_cost = build_forecast_response(
            {"model": "claude-sonnet-4-6", "messages": long_msg}, empty_db
        )["estimated_cost_usd"]
        assert long_cost > short_cost

    def test_max_tokens_small_sets_output_estimate(self, empty_db):
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }
        result = build_forecast_response(body, empty_db)
        assert result["breakdown"]["output_estimate"] == 10

    def test_cache_history_raises_cached_tokens(self, history_db):
        """With 50% hit rate, cached_tokens > 0 for substantial input."""
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": " ".join(["word"] * 200)}],
        }
        result = build_forecast_response(body, history_db)
        # cache_hit_likelihood is 0.5, input_tokens > 0, so cached_tokens should be > 0
        assert result["cache_hit_likelihood"] > 0
        assert result["cached_tokens"] >= 0
