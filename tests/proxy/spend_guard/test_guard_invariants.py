# SPDX-License-Identifier: Apache-2.0
"""Regression probes for cache-independent anchors and approval safety."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tokenpak.proxy.spend_guard import orchestrator, usage_anchor
from tokenpak.proxy.spend_guard.estimator import estimate, structural_tokens
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

MODEL = "claude-opus-5-5"
HEADERS = {"X-TokenPak-Agent": "fixture"}


def wire(value):
    return json.dumps(value, separators=(",", ":")).encode()


def request(text, model=MODEL):
    return {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": text}]}


def reply(text):
    value = request("x" * 3680)
    value["messages"] += [
        {"role": "assistant", "content": "approval required"},
        {"role": "user", "content": text},
    ]
    return value


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    usage_anchor.reset_for_testing()
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 1000
    )
    config = SpendGuardConfig()
    config.audit_db_path = str(tmp_path / "guard.db")
    config.rolling_caps_enabled = False
    yield config
    usage_anchor.reset_for_testing()


def run(value, cfg, *, headers=HEADERS, session="session"):
    return orchestrator.evaluate(wire(value), value["model"], session, headers, config=cfg)


def expire(cfg):
    with sqlite3.connect(cfg.audit_db_path) as db:
        db.execute("UPDATE pending_requests SET expires_at=0, status='expired'")


@pytest.mark.parametrize("placement", ["block", "message", "nested"])
def test_anchor_matches_content_when_cache_markers_move(cfg, monkeypatch, placement):
    """Regression: moving cache markers must not count the entire history twice."""
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 1000000
    )
    prior = request([{"type": "text", "text": "x" * 1240000}])
    marker = {"type": "ephemeral"}
    if placement == "message":
        prior["messages"][0]["cache_control"] = marker
    elif placement == "nested":
        prior["messages"][0]["content"] = [
            {"type": "tool_result", "content": prior["messages"][0]["content"]}
        ]
        prior["messages"][0]["content"][0]["content"][0]["cache_control"] = marker
    else:
        prior["messages"][0]["content"][0]["cache_control"] = marker
    usage_anchor.record_completed(
        wire(prior), MODEL, "session", {"input_tokens": 700000}, provider="anthropic", started_at=1
    )
    current = json.loads(wire(prior))
    stack = [current]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            item.pop("cache_control", None)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    suffix = {
        "role": "user",
        "content": [{"type": "text", "text": "continue", "cache_control": marker}],
    }
    current["messages"].append(suffix)
    expected = max(
        structural_tokens(current, MODEL)[0],
        700000 + structural_tokens({"messages": [suffix]}, MODEL)[0],
    )
    result = run(current, cfg)
    assert result.decision.risk.projected_input_tokens == expected
    assert 0.8 * 700000 <= expected <= 1.5 * 700000
    assert result.kind in ("forward", "forward_modified")
    assert usage_anchor.projection_floor(wire(current), MODEL, "foreign") == 0
    assert usage_anchor.projection_floor(wire(current), "foreign-model", "session") == 0


@pytest.mark.parametrize("cap", ["rolling", "session", "window"])
@pytest.mark.parametrize(
    "approval", ["yes", "[TIP: allow=once]", "[TIP: allow=15m]", "[TIP: allow=session]"]
)
@pytest.mark.parametrize("expired", [False, True])
def test_all_approval_paths_recheck_caps(cfg, monkeypatch, cap, approval, expired):
    """Regression: an approval only releases the context soft block."""
    assert run(request("x" * 3680), cfg).kind == "block"
    if expired:
        expire(cfg)
    if cap == "rolling":
        cfg.rolling_caps_enabled = True
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.rolling_caps.record_session_agent", lambda *a: None
        )
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.rolling_caps.check_rolling_caps_and_admit",
            lambda **kw: (SimpleNamespace(cap_dimension="cost"), None),
        )
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.block_response.build_rolling_cap_block",
            lambda breach: b"{}",
        )
    elif cap == "session":
        cfg.session_block_cost_usd = 1
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard.session_state.session_cumulative_cost", lambda *a, **kw: 5
        )
    else:
        monkeypatch.setattr(
            "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 900
        )
    result = run(reply(approval), cfg)
    assert result.kind in ("block", "hard_block")
    assert result.http_status == 402


@pytest.mark.parametrize("depth", [500, 600])
def test_deep_content_is_estimated_and_hard_blocked(cfg, depth):
    """Regression: deep content must not raise into a passthrough."""
    inner = {"type": "text", "text": "x" * 4100000}
    for _ in range(depth):
        inner = {"k": inner}
    value = request([{"type": "tool_result", "content": [inner]}])
    assert estimate(wire(value), MODEL).projected_input_tokens >= 1025000
    assert run(value, cfg).kind == "hard_block"


@pytest.mark.parametrize("error", [ValueError, RuntimeError, RecursionError, MemoryError])
def test_any_estimator_failure_blocks(cfg, monkeypatch, error):
    """Regression: unmeasurable requests never reach a provider."""

    def fail(*args, **kwargs):
        raise error("synthetic estimator failure")

    monkeypatch.setattr(orchestrator, "run_estimate", fail)
    result = run(request("hello"), cfg)
    assert result.kind == "block"
    assert result.http_status == 402
    assert result.body is None


@pytest.mark.parametrize("scope", ["same", "model", "agent", "session"])
@pytest.mark.parametrize("text", ["yes", "no", "[TIP: allow=once]", "[TIP: cancel]", "new topic"])
def test_expired_holds_never_strand_a_session(cfg, scope, text):
    """Regression: every human request can supersede an expired hold."""
    held = request("x" * 3680, model="other-model" if scope == "model" else MODEL)
    blocked = run(
        held,
        cfg,
        headers={"X-TokenPak-Agent": "other"} if scope == "agent" else HEADERS,
        session="other-session" if scope == "session" else "session",
    )
    assert blocked.kind == "block"
    expire(cfg)
    result = run(request(text), cfg)
    assert result.kind in ("forward", "forward_modified", "cancel")
    if scope != "session":
        assert PendingStore(cfg.audit_db_path).get_by_id(blocked.pending_id).status in (
            "consumed",
            "discarded",
        )
    assert run(request("next topic"), cfg).kind == "forward"


@pytest.mark.parametrize("extra,expected", [(994, "forward"), (995, "block")])
def test_late_approval_preserves_exact_growth_boundary(cfg, monkeypatch, extra, expected):
    """Regression: an expired 8000-token hold permits 9000, but not 9001."""
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 10000
    )
    cfg.default_context_window_percent = 80
    held = request("x" * 32000)
    assert run(held, cfg).kind == "block"
    expire(cfg)
    held["messages"] += [
        {"role": "assistant", "content": "approval required"},
        {"role": "user", "content": "qqqq"},
        {"role": "assistant", "content": "f" * (4 * extra)},
        {"role": "user", "content": "yes"},
    ]
    assert structural_tokens(held, MODEL)[0] == 8006 + extra
    assert run(held, cfg).kind == expected
    assert run(held, cfg).kind == "block"


def test_expired_used_reply_is_superseded_without_another_approval(cfg):
    """A used approval cannot pin an expired hold or authorize a fresh one."""
    from tokenpak.proxy.spend_guard.approvals import ContextApprovals, scope_key
    from tokenpak.proxy.spend_guard.intent import approval_fingerprint

    blocked = run(request("x" * 3680), cfg)
    expire(cfg)
    value = request("yes")
    ContextApprovals(PendingStore(cfg.audit_db_path).path).remember_reply(
        scope_key("session", MODEL, HEADERS), approval_fingerprint(wire(value))
    )
    assert run(value, cfg).kind == "forward"
    assert PendingStore(cfg.audit_db_path).get_by_id(blocked.pending_id).status == "discarded"


def test_active_holds_are_scoped_and_other_scope_settlement_does_not_hide_them(cfg):
    """A newer hold in another scope must not mask a still-active local hold."""
    first = run(request("x" * 3680), cfg)
    second = run(request("x" * 3680, model="other-model"), cfg)
    assert first.kind == second.kind == "block"
    assert run(request("yes", model="other-model"), cfg).kind == "replay"
    result = run(request("yes"), cfg)
    assert result.kind == "replay"
    assert result.pending_id == first.pending_id


def test_cache_marker_move_at_seventy_percent_observed_usage(cfg, monkeypatch):
    """Regression: the 700000-token cache-marker repro projects 700003, not 1010004."""
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 1000000
    )
    history = [{"role": "user", "content": "begin"}]
    for _ in range(40):
        for role in ("assistant", "user"):
            history.append({"role": role, "content": [{"type": "text", "text": "x" * 15500}]})
    prior = {"model": MODEL, "max_tokens": 32000, "messages": json.loads(wire(history))}
    prior["messages"][-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    usage_anchor.record_completed(
        wire(prior),
        MODEL,
        "session",
        {"input_tokens": 2000, "cache_read_input_tokens": 698000},
        provider="anthropic",
        started_at=1,
    )
    current = {
        **prior,
        "messages": history
        + [
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "continue", "cache_control": {"type": "ephemeral"}}
                ],
            },
        ],
    }
    result = run(current, cfg)
    assert result.kind == "forward"
    assert result.decision.risk.projected_input_tokens == 700003
