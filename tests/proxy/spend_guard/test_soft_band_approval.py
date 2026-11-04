# SPDX-License-Identifier: Apache-2.0
"""Regression: soft-band approvals replay once; hard limits never create holds."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tokenpak.proxy.spend_guard import orchestrator, usage_anchor
from tokenpak.proxy.spend_guard._context_window import get_model_max_context
from tokenpak.proxy.spend_guard.contracts import RiskEstimate
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
from tokenpak.proxy.spend_guard.rolling_caps import CapBreach

MODEL = "gpt-5.5-codex"
HEADERS = {"X-TokenPak-Agent": "fixture"}
APPROVALS = [
    "yes",
    "[TIP: allow=once]",
    "[TIP: allow=15m]",
    "[TIP: allow=session]",
    "[TIP: bypass=on]",
]


def wire(value):
    return json.dumps(value, separators=(",", ":")).encode()


def request(text="synthetic request"):
    return {"model": MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": text}]}


def run(value, cfg):
    return orchestrator.evaluate(
        wire(value),
        MODEL,
        "session",
        HEADERS,
        config=cfg,
        target_url="https://example.invalid/v1/responses",
    )


def rows(cfg):
    with sqlite3.connect(cfg.audit_db_path) as db:
        return db.execute("SELECT pending_id, status FROM pending_requests").fetchall()


@pytest.fixture
def cfg(tmp_path):
    usage_anchor.reset_for_testing()
    yield SpendGuardConfig(audit_db_path=str(tmp_path / "guard.db"), rolling_caps_enabled=False)
    usage_anchor.reset_for_testing()


def fake_estimate(monkeypatch, tokens=600_000, cost=0.1):
    monkeypatch.setattr(
        orchestrator,
        "run_estimate",
        lambda *a, **kw: RiskEstimate(MODEL, 0, tokens, tokens, 0, cost, 0),
    )
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda _: None
    )


@pytest.mark.parametrize("approval", ["yes", "[TIP: allow=once]"])
def test_unknown_window_responses_real_estimator_replays_once(cfg, approval):
    """Regression: Codex output allowance pushes this request into the fallback band."""
    assert get_model_max_context(MODEL) is None
    original = {"model": MODEL, "input": "x" * 1_500_012, "max_output_tokens": 128_000}
    first = run(original, cfg)
    assert first.kind == "block"
    assert first.decision.reason == "projected_tokens_exceeded"
    assert first.decision.risk.projected_input_tokens == 375_003
    assert first.decision.risk.projected_output_tokens == 128_000
    result = run({"model": MODEL, "input": approval}, cfg)
    assert result.kind == "replay"
    assert result.body == wire(original)
    assert result.headers == HEADERS
    assert result.target_url == "https://example.invalid/v1/responses"
    assert rows(cfg) == [(first.pending_id, "consumed")]
    assert run({"model": MODEL, "input": approval}, cfg).kind != "replay"
    assert rows(cfg) == [(first.pending_id, "consumed")]


@pytest.mark.parametrize("band", ["tokens", "dollars"])
@pytest.mark.parametrize("approval", ["yes", "[TIP: allow=once]"])
def test_soft_band_held_chat_replays_without_stacking(cfg, monkeypatch, band, approval):
    """Regression: yes and one-shot TIP release both legacy soft bands."""
    fake_estimate(
        monkeypatch,
        tokens=600_000 if band == "tokens" else 100,
        cost=6 if band == "dollars" else 0.1,
    )
    cfg.block_cost_usd = 5 if band == "dollars" else 0
    original = request()
    first = run(original, cfg)
    assert first.kind == "block"
    assert first.decision.reason == (
        "projected_tokens_exceeded" if band == "tokens" else "projected_cost_exceeded"
    )
    result = run(request(approval), cfg)
    assert result.kind == "replay"
    assert result.body == wire(original)
    assert rows(cfg) == [(first.pending_id, "consumed")]
    assert run(request(approval), cfg).kind != "replay"
    assert sum(status == "consumed" for _, status in rows(cfg)) == 1


def engage_cap(cfg, monkeypatch, cap):
    if cap == "window":
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda _: 100
        )
    elif cap == "session":
        cfg.session_block_cost_usd = 5
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.session_state.session_cumulative_cost", lambda *a, **kw: 5
        )
    else:
        cfg.rolling_caps_enabled = True
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.rolling_caps.record_session_agent", lambda *a: None
        )
        breach = CapBreach("per_agent_cost_usd", "fixture", 3600, 6, 5, 0.1, 120)
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.rolling_caps.check_rolling_caps_and_admit",
            lambda **kw: (breach, None),
        )


def assert_unliftable(result):
    assert result.kind in ("block", "hard_block")
    assert result.http_status == 402
    assert result.decision.requires_approval is False
    error = json.loads(result.response_body)["error"]
    assert error["requires_approval"] is False
    assert "reply 'yes'" not in error["message"].lower()
    assert "[TIP:" not in error["message"]
    assert not error.get("approval_prompt")
    assert not error.get("bypass_directive")
    assert not error.get("pending_id")


@pytest.mark.parametrize("cap", ["window", "rolling", "session"])
def test_unliftable_cap_offers_no_approval_and_never_stores_hold(cfg, monkeypatch, cap):
    """Regression: a terminal cap response must not invite an impossible approval."""
    fake_estimate(monkeypatch, tokens=100)
    engage_cap(cfg, monkeypatch, cap)
    for text in ("original", "yes", "[TIP: allow=once]"):
        assert_unliftable(run(request(text), cfg))
        assert rows(cfg) == []


@pytest.mark.parametrize("cap", ["window", "rolling", "session"])
@pytest.mark.parametrize("approval", APPROVALS)
@pytest.mark.parametrize("phase", ["fresh", "live", "late"])
def test_approval_cap_matrix_never_adds_a_hold(cfg, monkeypatch, cap, approval, phase):
    """All 45 fresh/live/late approval paths retain the hard-cap boundary."""
    fake_estimate(monkeypatch, tokens=100)
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda _: 110
    )
    if phase != "fresh":
        assert run(request(), cfg).kind == "block"
        if phase == "late":
            with sqlite3.connect(cfg.audit_db_path) as db:
                db.execute("UPDATE pending_requests SET expires_at=0, status='expired'")
    before = rows(cfg) if phase != "fresh" else []
    engage_cap(cfg, monkeypatch, cap)
    assert_unliftable(run(request(approval), cfg))
    assert rows(cfg) == before


@pytest.mark.parametrize("band", ["tokens", "dollars"])
@pytest.mark.parametrize("scope", ["15m", "session"])
def test_persistent_grants_keep_original_band_scope_without_stacking(cfg, monkeypatch, band, scope):
    """Persistent grants still cover only context percent; another scope cannot stack holds."""
    fake_estimate(
        monkeypatch,
        tokens=600_000 if band == "tokens" else 100,
        cost=6 if band == "dollars" else 0.1,
    )
    cfg.block_cost_usd = 5 if band == "dollars" else 0
    first = run(request(), cfg)
    for _ in range(2):
        result = run(request(f"[TIP: allow={scope}]"), cfg)
        assert result.kind == "block"
        assert result.pending_id == first.pending_id
        assert rows(cfg) == [(first.pending_id, "pending")]
    assert run(request("yes"), cfg).kind == "replay"
    assert PendingStore(cfg.audit_db_path).get_by_id(first.pending_id).status == "consumed"
