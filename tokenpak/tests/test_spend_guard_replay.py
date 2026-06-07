# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.proxy.spend_guard.replay."""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.spend_guard import block_response
from tokenpak.proxy.spend_guard.contracts import TIPDirective
from tokenpak.proxy.spend_guard.intent import Intent
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
from tokenpak.proxy.spend_guard.replay import resolve_pending


@pytest.fixture
def store(tmp_path):
    return PendingStore(str(tmp_path / "spend_guard.db"))


_BUILDERS = {
    "cancelled": block_response.cancelled,
    "reprompt": block_response.reprompt,
    "pending_waiting": block_response.pending_waiting,
}


def _make_pending(store, body=b'original-body-bytes', headers=None,
                  target_url="https://api.anthropic.com/v1/messages"):
    return store.store(
        session_id="sess-X",
        body=body,
        headers=headers or {"x-api-key": "abc"},
        target_url=target_url,
        provider="anthropic",
        model="claude-opus-4-7",
        projected_tokens=600_000,
        projected_cost_usd=12.0,
        ttl_seconds=600,
    )


class TestPositiveIntentReplay:
    def test_replay_returns_original_bytes(self, store):
        original = b'{"model":"opus","messages":[{"role":"user","content":"original"}]}'
        p = _make_pending(store, body=original)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.POSITIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "replay"
        assert out.body == original  # byte-identical
        assert out.audit_event == "replay"

    def test_replay_returns_original_headers(self, store):
        hdrs = {"X-Api-Key": "key123", "anthropic-version": "2023-06-01"}
        p = _make_pending(store, headers=hdrs)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.POSITIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.headers == hdrs

    def test_replay_includes_target_url(self, store):
        p = _make_pending(store, target_url="https://example.com/v1/messages")
        out = resolve_pending(
            store=store, pending=p, intent=Intent.POSITIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.target_url == "https://example.com/v1/messages"

    def test_replay_consumes_pending(self, store):
        p = _make_pending(store)
        out1 = resolve_pending(
            store=store, pending=p, intent=Intent.POSITIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out1.kind == "replay"
        # Second attempt — already consumed, falls back to pending_waiting block.
        out2 = resolve_pending(
            store=store, pending=p, intent=Intent.POSITIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out2.kind == "block"
        assert out2.audit_event == "replay_race"


class TestNegativeIntentCancel:
    def test_cancel_returns_acknowledgment(self, store):
        p = _make_pending(store)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.NEGATIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "cancel"
        assert out.http_status == 200
        body = json.loads(out.response_body)
        assert body["spend_guard"]["type"] == "tokenpak_spend_guard_cancelled"

    def test_cancel_marks_discarded(self, store):
        p = _make_pending(store)
        resolve_pending(
            store=store, pending=p, intent=Intent.NEGATIVE, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert store.get_by_session("sess-X") is None


class TestAmbiguousReprompt:
    def test_ambiguous_keeps_pending(self, store):
        p = _make_pending(store)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.AMBIGUOUS, tip=None,
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "reprompt"
        assert out.http_status == 402
        # Pending still findable
        assert store.get_by_session("sess-X") is not None


class TestTIPAuthorize:
    def test_tip_allow_once_replays_even_on_ambiguous(self, store):
        p = _make_pending(store)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.AMBIGUOUS,
            tip=TIPDirective(allow_scope="once", max_cost_usd=20.0),
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "replay"

    def test_tip_negative_intent_still_cancels(self, store):
        # User both said "no" AND included `[TIP: allow=once]`. Negative
        # intent wins — explicit cancel beats explicit allow.
        p = _make_pending(store)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.NEGATIVE,
            tip=TIPDirective(allow_scope="once"),
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "cancel"

    def test_tip_bypass_replays(self, store):
        p = _make_pending(store)
        out = resolve_pending(
            store=store, pending=p, intent=Intent.AMBIGUOUS,
            tip=TIPDirective(bypass=True),
            cfg=SpendGuardConfig(), builders=_BUILDERS,
        )
        assert out.kind == "replay"
