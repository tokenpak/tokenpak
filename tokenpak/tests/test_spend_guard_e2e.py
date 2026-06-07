# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the full TIP Spend Guard pipeline.

Drives ``evaluate()`` (the orchestrator) directly. Verifies that all the
primitives compose correctly: estimator → policy →
pending → block_response → intent → replay → tip_header → audit.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.spend_guard import evaluate
from tokenpak.proxy.spend_guard.audit import query_recent
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolated audit/pending DB so e2e tests don't touch live state."""
    db = str(tmp_path / "spend_guard.db")
    # Force the orchestrator's SpendGuardConfig to use our temp DB by
    # disabling session-cumulative (which would read live monitor.db) and
    # pointing audit_db_path at a writable temp.
    cfg = SpendGuardConfig()
    cfg.audit_db_path = db
    cfg.session_block_cost_usd = 0.0  # isolate per-request behavior
    return cfg, db


def _opus_body(content: str | int = "hi", max_tokens: int = 4000) -> bytes:
    if isinstance(content, int):
        content = "x" * content
    return json.dumps({
        "model": "claude-opus-4-7",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }).encode()


class TestSmallRequestForwards:
    def test_small_request_passthrough(self, tmp_db):
        cfg, _ = tmp_db
        out = evaluate(_opus_body("hi", 1000), "claude-opus-4-7", "sess-1", {},
                       config=cfg)
        assert out.kind == "forward"
        assert out.body is not None


class TestRunawayBlocks:
    def test_runaway_blocks_then_replay(self, tmp_db):
        cfg, _ = tmp_db
        # 600K-token user message → blocks
        runaway = _opus_body(740_000, max_tokens=4000)
        out_block = evaluate(runaway, "claude-opus-4-7", "sess-replay", {},
                             config=cfg)
        assert out_block.kind == "block"
        # Approve
        yes = _opus_body("yes")
        out_replay = evaluate(yes, "claude-opus-4-7", "sess-replay", {},
                              config=cfg)
        assert out_replay.kind == "replay"
        # Body must be byte-identical to the original runaway request
        assert out_replay.body == runaway

    def test_runaway_blocks_then_cancel(self, tmp_db):
        cfg, _ = tmp_db
        runaway = _opus_body(740_000, max_tokens=4000)
        out_block = evaluate(runaway, "claude-opus-4-7", "sess-cancel", {},
                             config=cfg)
        assert out_block.kind == "block"
        no = _opus_body("no")
        out_cancel = evaluate(no, "claude-opus-4-7", "sess-cancel", {},
                              config=cfg)
        assert out_cancel.kind == "cancel"
        # Session unblocked — fresh small request forwards
        out_after = evaluate(_opus_body("hello"), "claude-opus-4-7",
                             "sess-cancel", {}, config=cfg)
        assert out_after.kind == "forward"

    def test_ambiguous_re_prompts(self, tmp_db):
        cfg, _ = tmp_db
        runaway = _opus_body(740_000, max_tokens=4000)
        evaluate(runaway, "claude-opus-4-7", "sess-amb", {}, config=cfg)
        out = evaluate(_opus_body("I'm thinking about it"), "claude-opus-4-7",
                       "sess-amb", {}, config=cfg)
        assert out.kind == "reprompt"


class TestTIPDirectivesE2E:
    def test_tip_allow_once_bypasses(self, tmp_db):
        cfg, _ = tmp_db
        # 740K chars / 4 ≈ 185K tokens → in [180K, 200K) → soft block
        # under v1.5.2 defaults (90% ≤ projected_input < 100%). TIP
        # allow=once with sufficient ceiling clears the soft block.
        body = _opus_body("[TIP: allow=once max=$15] " + "x" * 740_000,
                          max_tokens=4000)
        out = evaluate(body, "claude-opus-4-7", "sess-tip-allow", {},
                       config=cfg)
        assert out.kind == "forward_modified"
        # Forwarded body has [TIP:...] stripped
        forwarded = json.loads(out.body.decode("utf-8"))
        assert "[TIP:" not in forwarded["messages"][0]["content"]

    def test_tip_estimate_returns_riskestimate(self, tmp_db):
        cfg, _ = tmp_db
        body = _opus_body("[TIP: estimate=on] " + "x" * 100_000)
        out = evaluate(body, "claude-opus-4-7", "sess-tip-est", {}, config=cfg)
        assert out.kind == "estimate"
        assert out.http_status == 200
        payload = json.loads(out.response_body)
        assert payload["spend_guard"]["type"] == "tokenpak_spend_guard_estimate"
        assert "estimate" in payload["spend_guard"]

    def test_tip_cancel_discards_pending(self, tmp_db):
        cfg, _ = tmp_db
        runaway = _opus_body(740_000)
        evaluate(runaway, "claude-opus-4-7", "sess-tip-cancel", {}, config=cfg)
        out = evaluate(_opus_body("[TIP: cancel] never mind"), "claude-opus-4-7",
                       "sess-tip-cancel", {}, config=cfg)
        assert out.kind == "cancel"


class TestHardBlock:
    def test_hard_block_not_bypassed_by_tip(self, tmp_db):
        cfg, _ = tmp_db
        # 4M chars / 4 = 1M tokens, which crosses hard_block_tokens=1M
        body = _opus_body("[TIP: bypass=on] " + "x" * 4_000_000,
                          max_tokens=50_000)
        out = evaluate(body, "claude-opus-4-7", "sess-hard", {}, config=cfg)
        assert out.kind == "hard_block"
        payload = json.loads(out.response_body)
        assert payload["error"]["type"] == "tokenpak_spend_guard_hard_blocked"
        assert payload["error"]["recovery_status"] == "terminally_blocked"


class TestAntiLoop:
    def test_repeated_blocked_request_anti_loops(self, tmp_db):
        cfg, _ = tmp_db
        # Use a different session for each iteration's first block; same
        # request_hash should hit the anti-loop cache. We verify by
        # checking the audit log for 'anti_loop_hit' events.
        runaway = _opus_body(740_000, max_tokens=4000)
        # First call — blocks normally, stores pending.
        evaluate(runaway, "claude-opus-4-7", "sess-loop-A", {}, config=cfg)
        # Different session, same body — anti-loop kicks in.
        for _ in range(3):
            out = evaluate(runaway, "claude-opus-4-7", f"sess-loop-{_}", {},
                           config=cfg)
            assert out.kind == "block"
        rows = query_recent(cfg.audit_db_path, limit=20)
        events = [r["event_type"] for r in rows]
        assert "anti_loop_hit" in events


class TestConcurrentSessions:
    def test_session_a_blocked_does_not_affect_session_b(self, tmp_db):
        cfg, _ = tmp_db
        runaway = _opus_body(740_000, max_tokens=4000)
        evaluate(runaway, "claude-opus-4-7", "sess-A", {}, config=cfg)
        # Session B small request — must still pass.
        out = evaluate(_opus_body("hi"), "claude-opus-4-7", "sess-B", {},
                       config=cfg)
        assert out.kind == "forward"


class TestDisabled:
    def test_enabled_false_passthrough(self):
        cfg = SpendGuardConfig()
        cfg.enabled = False
        runaway = _opus_body(4_000_000, max_tokens=50_000)
        out = evaluate(runaway, "claude-opus-4-7", "sess-disabled", {},
                       config=cfg)
        assert out.kind == "forward"
        assert out.body == runaway


class TestAuditTrail:
    def test_block_writes_audit_row(self, tmp_db):
        cfg, db = tmp_db
        runaway = _opus_body(740_000)
        evaluate(runaway, "claude-opus-4-7", "sess-audit", {}, config=cfg)
        rows = query_recent(db, session_id="sess-audit", limit=10)
        assert any(r["event_type"] == "block" for r in rows)

    def test_replay_writes_audit_row(self, tmp_db):
        cfg, db = tmp_db
        runaway = _opus_body(740_000)
        evaluate(runaway, "claude-opus-4-7", "sess-audit2", {}, config=cfg)
        evaluate(_opus_body("yes"), "claude-opus-4-7", "sess-audit2", {},
                 config=cfg)
        rows = query_recent(db, session_id="sess-audit2", limit=10)
        events = [r["event_type"] for r in rows]
        assert "block" in events
        assert "replay" in events
